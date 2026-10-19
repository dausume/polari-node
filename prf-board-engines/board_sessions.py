"""
board_sessions — sim-0 (HARDWARE_BENCH_CONVERGENCE_PLAN.md §8): twin SESSIONS on the board-engines worker.

`/run` is one subprocess per request; a session is a DETACHED `polari-avr-twin` that outlives the request:

  POST   /sessions        {hex_b64, board?, mcu?, freq?, ttl_s?, token?, owner?, free?}
                          -> {ok, id, token, ports: {tcp, ctl}, hex_sha256, expires, ...}  (the token is shown ONCE, here)
  GET    /sessions        {ok, sessions: [..], ports, max}        (no tokens)
  GET    /sessions/{id}   one session + the twin's LATEST status line (read from its stdout file)
  DELETE /sessions/{id}   stop it now

State is one file per session under SESSIONS_DIR (default /srv/sessions): <id>.json (pid, ports, hex sha, started, ttl, owner,
token - mode 0600), <id>.hex, <id>.out (the twin's stdout+stderr). A reaper kills sessions past their TTL on EVERY request and
every REAP_EVERY_S seconds from a thread (an abandoned session must not outlive its TTL just because nobody asked). The twin
gets its token through the environment (POLARI_TWIN_CTL_TOKEN), never argv. Ports come from SESSION_PORTS (default 9832-9863
minus the other hw-engines workers' published ports 9840/9850/9860).
"""
import base64
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time

import falcon

DIR = os.environ.get('SESSIONS_DIR', '/srv/sessions')
TWIN = os.environ.get('SESSIONS_TWIN_BIN', 'polari-avr-twin')
RESERVED = {int(p) for p in os.environ.get('SESSION_PORTS_RESERVED', '9840,9850,9860').split(',') if p}
LO, HI = [int(x) for x in os.environ.get('SESSION_PORTS', '9832-9863').split('-')]
PORTS = [p for p in range(LO, HI + 1) if p not in RESERVED]
MAX_SESSIONS = int(os.environ.get('SESSIONS_MAX', '8'))
TTL_DEFAULT, TTL_MAX, TTL_MIN = 1800, 14400, 10
KEEP_DEAD_S = 300
REAP_EVERY_S = 5
MAX_HEX = 1024 * 1024
MCUS = {'atmega328p', 'atmega328', 'atmega168', 'atmega32u4', 'atmega16u2'}
_lock = threading.RLock()
_procs = {}   # id -> Popen (so a finished child is wait()ed, not left a zombie)


def _path(sid, ext):
    return os.path.join(DIR, '%s.%s' % (sid, ext))


def _load(sid):
    try:
        return json.load(open(_path(sid, 'json')))
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
        return 'polari-avr-twin' in open('/proc/%d/cmdline' % s['pid']).read()
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
    for ext in ('json', 'json.tmp', 'hex', 'out'):
        try:
            os.remove(_path(sid, ext))
        except OSError:
            pass


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
        head = open(_path(sid, 'out'), 'rb').read(2048).decode('utf-8', 'replace').splitlines()
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


def start(body):
    """Returns (status, payload)."""
    reap()
    with _lock:
        if not isinstance(body, dict) or not body.get('hex_b64'):
            return falcon.HTTP_400, {'ok': False, 'error': 'POST /sessions needs hex_b64 (the firmware .hex, base64)'}
        try:
            raw = base64.b64decode(body['hex_b64'], validate=True)
        except Exception:
            return falcon.HTTP_400, {'ok': False, 'error': 'hex_b64 is not base64'}
        if len(raw) > MAX_HEX or not raw.startswith(b':'):
            return falcon.HTTP_400, {'ok': False, 'error': 'the firmware must be Intel HEX text of at most %d bytes' % MAX_HEX}
        mcu = str(body.get('mcu') or 'atmega328p')
        if mcu not in MCUS:
            return falcon.HTTP_400, {'ok': False, 'error': 'mcu %r is not one of %s' % (mcu, sorted(MCUS))}
        board = re.sub(r'[^A-Za-z0-9_.-]', '', str(body.get('board') or 'unknown'))[:60] or 'unknown'
        owner = re.sub(r'[^A-Za-z0-9_.@:-]', '', str(body.get('owner') or ''))[:80]
        try:
            freq = int(body.get('freq') or 16000000)
            ttl = int(body.get('ttl_s') or TTL_DEFAULT)
        except (TypeError, ValueError):
            return falcon.HTTP_400, {'ok': False, 'error': 'freq and ttl_s are whole numbers'}
        if not 1000000 <= freq <= 32000000:
            return falcon.HTTP_400, {'ok': False, 'error': 'freq must be 1 MHz to 32 MHz'}
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
        hex_path = _path(sid, 'hex')
        os.makedirs(DIR, exist_ok=True)
        open(hex_path, 'wb').write(raw)
        realtime = not body.get('free')
        argv = [TWIN, '--hex', hex_path, '--mcu', mcu, '--freq', str(freq), '--tcp', str(ports[0]), '--ctl', str(ports[1]), '--status-ms', '500']
        if not realtime:
            argv.append('--free')
        out = open(_path(sid, 'out'), 'wb')
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                env=dict(os.environ, POLARI_TWIN_CTL_TOKEN=token))
        out.close()
        _procs[sid] = proc
        started = time.time()
        s = {'id': sid, 'board': board, 'mcu': mcu, 'freq': freq, 'hex_sha256': hashlib.sha256(raw).hexdigest(), 'hex_bytes': len(raw),
             'ports': {'tcp': ports[0], 'ctl': ports[1]}, 'owner': owner, 'started': started, 'ttl_s': ttl, 'expires': started + ttl,
             'pid': proc.pid, 'realtime': realtime, 'token': token}
        _save(s)
        for _ in range(100):   # up to 5 s for the {"t":"ready"} line
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
    return {'available': bool(__import__('shutil').which(TWIN)), 'max': MAX_SESSIONS, 'active': len(live),
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
    threading.Thread(target=_loop, daemon=True, name='session-reaper').start()
