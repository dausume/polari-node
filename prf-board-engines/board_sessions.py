"""
board_sessions — sim-0 (HARDWARE_BENCH_CONVERGENCE_PLAN.md §8): twin SESSIONS on the board-engines worker.

`/run` is one subprocess per request; a session is a DETACHED `polari-avr-twin` that outlives the request:

  POST   /sessions        {hex_b64, board?, mcu?, freq?, ttl_s?, token?, owner?, free?}
                          -> {ok, id, token, ports: {tcp, ctl}, hex_sha256, expires, ...}  (the token is shown ONCE, here)
  GET    /sessions        {ok, sessions: [..], ports, max}        (no tokens)
  GET    /sessions/{id}   one session + the twin's LATEST status line (read from its stdout file)
  DELETE /sessions/{id}   stop it now
  POST   /sessions/{id}/check {token}  td-0: 200 {ok, id, ports, expires} when that is the running session's token; 403 wrong
                          token, 404 unknown id, 410 ended. The twin door (twin_door.py) asks this before it joins a caller to
                          a session port; the worker stays the only holder of tokens.

State is one file per session under SESSIONS_DIR (default /srv/sessions): <id>.json (pid, ports, hex sha, started, ttl, owner,
token - mode 0600), <id>.hex, <id>.out (the twin's stdout+stderr). A reaper kills sessions past their TTL on EVERY request and
every REAP_EVERY_S seconds from a thread (an abandoned session must not outlive its TTL just because nobody asked). The twin
gets its token through the environment (POLARI_TWIN_CTL_TOKEN), never argv. Ports come from SESSION_PORTS (default 9832-9863
minus the other hw-engines workers' published ports 9840/9850/9860).

c3g-2 (C3_SESSION_PLAN.md): ONE sessions module for every worker that runs twins. SESSIONS_KIND says which twin this worker starts:
  avr (the default)  polari-avr-twin, the firmware as hex_b64 (Intel HEX) - everything above
  c3                 polari-c3-twin (prf-esp-engines), which speaks the same control line. POST /sessions takes
                     {image_b64 (the merged flash image), layout (the pin layout's JSON text), wires? ("0-1"), board?, ttl_s?, token?, owner?}.
                     The reply keeps its keys: hex_sha256 / hex_bytes are the IMAGE's sha256 and size, mcu is esp32c3, freq is 0.
The esp worker's image takes this file from here at build time; it is never copied into that folder.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time

import falcon

DIR = os.environ.get('SESSIONS_DIR', '/srv/sessions')
KIND = os.environ.get('SESSIONS_KIND', 'avr')
TWINS = {'avr': 'polari-avr-twin', 'c3': 'polari-c3-twin'}
if KIND not in TWINS:
    raise SystemExit('SESSIONS_KIND=%s is not one of %s' % (KIND, ', '.join(sorted(TWINS))))
TWIN = os.environ.get('SESSIONS_TWIN_BIN', TWINS[KIND])
READY_WAIT_S = {'avr': 5.0, 'c3': 30.0}[KIND]      # the C3 twin boots QEMU and opens its pin channel before it says ready
RESERVED = {int(p) for p in os.environ.get('SESSION_PORTS_RESERVED', '9840,9850,9860').split(',') if p}
LO, HI = [int(x) for x in os.environ.get('SESSION_PORTS', '9832-9863').split('-')]
PORTS = [p for p in range(LO, HI + 1) if p not in RESERVED]
MAX_SESSIONS = int(os.environ.get('SESSIONS_MAX', '8'))
TTL_DEFAULT, TTL_MAX, TTL_MIN = 1800, 14400, 10
KEEP_DEAD_S = 300
REAP_EVERY_S = 5
MAX_HEX = 1024 * 1024
MAX_IMAGE = 16 * 1024 * 1024       # the C3's merged flash image (4 MB for the tree's builds)
MAX_LAYOUT = 2 * 1024 * 1024
MCUS = {'atmega328p', 'atmega328', 'atmega168', 'atmega32u4', 'atmega16u2'}
_lock = threading.RLock()
_procs = {}   # id -> Popen (so a finished child is wait()ed, not left a zombie)


def _path(sid, ext):
    return os.path.join(DIR, '%s.%s' % (sid, ext))


def _load(sid):
    try:
        with open(_path(sid, 'json')) as f:
            return json.load(f)
    except Exception:
        return None


def _save(s):
    p = _path(s['id'], 'json')
    fd = os.open(p + '.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(s, f)
    os.replace(p + '.tmp', p)


def _alive(s):
    p = _procs.get(s['id'])
    if p is not None:
        return p.poll() is None
    try:
        os.kill(s['pid'], 0)
        with open('/proc/%d/cmdline' % s['pid']) as f:
            return os.path.basename(TWIN) in f.read()
    except Exception:
        return False


def _all():
    os.makedirs(DIR, exist_ok=True)
    out = []
    for fn in sorted(os.listdir(DIR)):
        if fn.endswith('.json'):
            s = _load(fn[:-5])
            if s:
                out.append(s)
    return out


def _kill(s):
    p = _procs.get(s['id'])
    try:
        os.killpg(s['pid'], signal.SIGTERM)
    except Exception:
        pass
    for _ in range(40):
        if not _alive(s):
            break
        time.sleep(0.05)
    else:
        try:
            os.killpg(s['pid'], signal.SIGKILL)
        except Exception:
            pass
        time.sleep(0.1)
    if p is not None:
        try:
            p.wait(timeout=2)
        except Exception:
            pass
    _procs.pop(s['id'], None)


def _remove(sid):
    for ext in ('json', 'json.tmp', 'hex', 'img', 'layout', 'out'):
        try:
            os.remove(_path(sid, ext))
        except OSError:
            pass
    shutil.rmtree(_path(sid, 'd'), ignore_errors=True)      # a C3 twin's own folder (its flash copy, its pin channel)


def reap():
    """Kill sessions past their TTL; mark twins that exited; forget the dead after KEEP_DEAD_S."""
    now = time.time()
    with _lock:
        for s in _all():
            if s.get('ended'):
                if now - s['ended'] > KEEP_DEAD_S:
                    _remove(s['id'])
            elif not _alive(s):
                s['ended'], s['end_reason'] = now, 'the twin process exited'
                _save(s)
            elif now >= s['expires']:
                _kill(s)
                s['ended'], s['end_reason'] = now, 'TTL reached (%ds): reaped' % s['ttl_s']
                _save(s)


def _tail_status(sid):
    """(latest status line or None, ready line or None, last 600 chars) from the twin's stdout file."""
    try:
        with open(_path(sid, 'out'), 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            lines = f.read().decode('utf-8', 'replace').splitlines()
            f.seek(0)
            head = f.read(2048).decode('utf-8', 'replace').splitlines()
    except Exception:
        return None, None, ''
    def last(prefix, ls):
        for ln in reversed(ls):
            if ln.startswith(prefix):
                try:
                    return json.loads(ln)
                except Exception:
                    return None
        return None
    return last('{"t":"status"', lines), last('{"t":"ready"', head), '\n'.join(lines[-5:])[-600:]


def _public(s, detail=False):
    now = time.time()
    out = {k: s[k] for k in ('id', 'board', 'mcu', 'freq', 'hex_sha256', 'hex_bytes', 'ports', 'owner', 'started', 'expires', 'ttl_s', 'pid', 'realtime')}
    out['kind'] = s.get('kind', 'avr')
    if 'wires' in s:
        out['wires'] = s['wires']
    out['state'] = 'ended' if s.get('ended') else 'running'
    out['remaining_s'] = 0 if s.get('ended') else max(0, round(s['expires'] - now, 1))
    if s.get('ended'):
        out['end_reason'] = s.get('end_reason', '')
    if detail:
        st, ready, tail = _tail_status(s['id'])
        out['status'], out['ready'], out['output_tail'] = st, ready, tail
    return out


def _free_ports(n):
    used = {p for s in _all() if not s.get('ended') for p in s['ports'].values()}
    free = [p for p in PORTS if p not in used]
    return free[:n] if len(free) >= n else None


def _avr(body, sid, ports):
    """The AVR twin of one request -> (error words or None, the record's firmware fields, argv)."""
    if not body.get('hex_b64'):
        return 'POST /sessions needs hex_b64 (the firmware .hex, base64)', None, None
    try:
        raw = base64.b64decode(body['hex_b64'], validate=True)
    except Exception:
        return 'hex_b64 is not base64', None, None
    if len(raw) > MAX_HEX or not raw.startswith(b':'):
        return 'the firmware must be Intel HEX text of at most %d bytes' % MAX_HEX, None, None
    mcu = str(body.get('mcu') or 'atmega328p')
    if mcu not in MCUS:
        return 'mcu %r is not one of %s' % (mcu, sorted(MCUS)), None, None
    try:
        freq = int(body.get('freq') or 16000000)
    except (TypeError, ValueError):
        return 'freq and ttl_s are whole numbers', None, None
    if not 1000000 <= freq <= 32000000:
        return 'freq must be 1 MHz to 32 MHz', None, None
    hex_path = _path(sid, 'hex')
    with open(hex_path, 'wb') as f:
        f.write(raw)
    realtime = not body.get('free')
    argv = [TWIN, '--hex', hex_path, '--mcu', mcu, '--freq', str(freq), '--tcp', str(ports[0]), '--ctl', str(ports[1]), '--status-ms', '500']
    if not realtime:
        argv.append('--free')
    return None, {'mcu': mcu, 'freq': freq, 'hex_sha256': hashlib.sha256(raw).hexdigest(), 'hex_bytes': len(raw), 'realtime': realtime}, argv


def _c3(body, sid, ports):
    """The C3 twin of one request (the same three values). The image runs on the pin model: the layout is the chip, the wires are the board."""
    if not body.get('image_b64') or not isinstance(body.get('layout'), str) or not body['layout'].strip():
        return 'POST /sessions needs image_b64 (the merged flash image, base64) and layout (the pin layout, JSON text)', None, None
    try:
        raw = base64.b64decode(body['image_b64'], validate=True)
    except Exception:
        return 'image_b64 is not base64', None, None
    if not 4096 <= len(raw) <= MAX_IMAGE or raw[:1] != b'\xe9':
        return 'the firmware must be a merged ESP flash image (first byte 0xE9) of at most %d bytes' % MAX_IMAGE, None, None
    if len(body['layout']) > MAX_LAYOUT:
        return 'the layout is over %d bytes' % MAX_LAYOUT, None, None
    try:
        json.loads(body['layout'])
    except Exception:
        return 'the layout is not JSON text (it is the pin layout, as the build wrote it)', None, None
    wires = str(body.get('wires') or '')
    if wires and not re.fullmatch(r'\d{1,2}-\d{1,2}(,\d{1,2}-\d{1,2})*', wires):
        return 'wires are pad-pad pairs (0-1,4-5), got %r' % wires[:60], None, None
    if body.get('free'):
        return 'the C3 twin runs in twin time only (no free-running mode)', None, None
    img, layout = _path(sid, 'img'), _path(sid, 'layout')
    with open(img, 'wb') as f:
        f.write(raw)
    with open(layout, 'w') as f:
        f.write(body['layout'])
    argv = [TWIN, '--image', img, '--layout', layout, '--wires', wires, '--tcp', str(ports[0]), '--ctl', str(ports[1]), '--status-ms', '500', '--work', _path(sid, 'd')]
    return None, {'mcu': 'esp32c3', 'freq': 0, 'hex_sha256': hashlib.sha256(raw).hexdigest(), 'hex_bytes': len(raw), 'realtime': True, 'wires': wires}, argv


def start(body):
    """Returns (status, payload)."""
    reap()
    with _lock:
        if not isinstance(body, dict):
            return falcon.HTTP_400, {'ok': False, 'error': 'POST /sessions needs a JSON object'}
        board = re.sub(r'[^A-Za-z0-9_.-]', '', str(body.get('board') or 'unknown'))[:60] or 'unknown'
        owner = re.sub(r'[^A-Za-z0-9_.@:-]', '', str(body.get('owner') or ''))[:80]
        try:
            ttl = int(body.get('ttl_s') or TTL_DEFAULT)
        except (TypeError, ValueError):
            return falcon.HTTP_400, {'ok': False, 'error': 'freq and ttl_s are whole numbers'}
        if not TTL_MIN <= ttl <= TTL_MAX:
            return falcon.HTTP_400, {'ok': False, 'error': 'ttl_s must be %d to %d seconds (%d is the default)' % (TTL_MIN, TTL_MAX, TTL_DEFAULT)}
        token = str(body.get('token') or secrets.token_hex(16))
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', token):
            return falcon.HTTP_400, {'ok': False, 'error': 'token must be 8 to 128 characters of letters, digits, - or _'}
        live = [s for s in _all() if not s.get('ended')]
        if len(live) >= MAX_SESSIONS:
            return falcon.HTTP_503, {'ok': False, 'error': 'this worker already runs %d sessions (the limit; stop one first)' % len(live)}
        ports = _free_ports(2)
        if not ports:
            return falcon.HTTP_503, {'ok': False, 'error': 'no free session ports left in %d-%d' % (LO, HI)}
        sid = secrets.token_hex(6)
        os.makedirs(DIR, exist_ok=True)
        why, fw, argv = {'avr': _avr, 'c3': _c3}[KIND](body, sid, ports)
        if why:
            _remove(sid)
            return falcon.HTTP_400, {'ok': False, 'error': why}
        out = open(_path(sid, 'out'), 'wb')
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                env=dict(os.environ, POLARI_TWIN_CTL_TOKEN=token))
        out.close()
        _procs[sid] = proc
        started = time.time()
        s = dict(fw, id=sid, board=board, kind=KIND, ports={'tcp': ports[0], 'ctl': ports[1]}, owner=owner, started=started, ttl_s=ttl, expires=started + ttl,
                 pid=proc.pid, token=token)
        _save(s)
        for _ in range(int(READY_WAIT_S / 0.05)):   # the {"t":"ready"} line
            _, ready, tail = _tail_status(sid)
            if ready or proc.poll() is not None:
                break
            time.sleep(0.05)
        if not ready:
            why = tail or 'no output'
            _kill(s)
            _remove(sid)
            return falcon.HTTP_500, {'ok': False, 'error': 'the twin did not become ready: %s' % why}
        return falcon.HTTP_200, dict(_public(s, detail=True), ok=True, token=token)


class Sessions:
    def on_get(self, req, resp):
        reap()
        resp.media = {'ok': True, 'sessions': [_public(s) for s in _all()], 'ports': '%d-%d minus %s' % (LO, HI, sorted(RESERVED)),
                      'max': MAX_SESSIONS, 'ttl_default_s': TTL_DEFAULT, 'ttl_max_s': TTL_MAX}

    def on_post(self, req, resp):
        try:
            body = json.load(req.bounded_stream)
        except Exception:
            body = None
        resp.status, resp.media = start(body)


class Session:
    def on_get(self, req, resp, sid):
        reap()
        s = _load(re.sub(r'[^0-9a-f]', '', sid))
        if not s:
            resp.status, resp.media = falcon.HTTP_404, {'ok': False, 'error': 'no session %s on this worker (ended long ago, or never started here)' % sid}
            return
        resp.media = dict(_public(s, detail=True), ok=True)

    def on_delete(self, req, resp, sid):
        with _lock:
            s = _load(re.sub(r'[^0-9a-f]', '', sid))
            if not s:
                resp.status, resp.media = falcon.HTTP_404, {'ok': False, 'error': 'no session %s on this worker' % sid}
                return
            if not s.get('ended'):
                _kill(s)
            _remove(s['id'])
            resp.media = {'ok': True, 'id': s['id'], 'stopped': True}


class Check:
    """td-0: the twin door's question - is this the token of a running session? Never says what the token is."""
    def on_post(self, req, resp, sid):
        reap()
        try:
            body = json.load(req.bounded_stream)
        except Exception:
            body = None
        token = body.get('token') if isinstance(body, dict) else None
        s = _load(re.sub(r'[^0-9a-f]', '', sid))
        if not s:
            resp.status, resp.media = falcon.HTTP_404, {'ok': False, 'error': 'no session %s on this worker' % sid}
        elif s.get('ended'):
            resp.status, resp.media = falcon.HTTP_410, {'ok': False, 'error': 'session %s has ended: %s' % (s['id'], s.get('end_reason', ''))}
        elif not isinstance(token, str) or not hmac.compare_digest(token.encode(), str(s.get('token') or '').encode()):
            resp.status, resp.media = falcon.HTTP_403, {'ok': False, 'error': 'the token is wrong for session %s' % s['id']}
        else:
            resp.media = {'ok': True, 'id': s['id'], 'ports': s['ports'], 'expires': s['expires']}


def transports():
    """td-0: the /capability `transports` block, or None. TWIN_DOOR_URL is the door's public address
    (wss://twin.<worker>.isle); a worker with no door configured advertises nothing and callers keep plain TCP."""
    base = os.environ.get('TWIN_DOOR_URL', '').strip().rstrip('/')
    if not re.fullmatch(r'wss?://[A-Za-z0-9.-]+(:\d+)?', base):
        return None
    return {'ctl': base + '/sessions/{id}/ctl', 'uart': base + '/sessions/{id}/uart'}


class Reaper:
    """falcon middleware: every request first reaps expired sessions."""
    def process_request(self, req, resp):
        try:
            reap()
        except Exception:
            pass


def info():
    """The /capability block: this worker lists sessions."""
    live = [s for s in _all() if not s.get('ended')]
    return {'available': bool(shutil.which(TWIN)), 'kind': KIND, 'max': MAX_SESSIONS, 'active': len(live),
            'ports': '%d-%d' % (LO, HI), 'reserved_ports': sorted(RESERVED), 'ttl_default_s': TTL_DEFAULT, 'ttl_max_s': TTL_MAX}


def _loop():
    while True:
        time.sleep(REAP_EVERY_S)
        try:
            reap()
        except Exception:
            pass


def add_routes(app):
    app.add_route('/sessions', Sessions())
    app.add_route('/sessions/{sid}', Session())
    app.add_route('/sessions/{sid}/check', Check())
    threading.Thread(target=_loop, daemon=True, name='session-reaper').start()
