"""
bridge_hosted - sim-2 (HARDWARE_BENCH_CONVERGENCE_PLAN.md s3/s4): HOSTED BRIDGES on the bridge-engines worker.

A hosted bridge is the generated Polari Hardware Bridge (the Java app grpcbridge generates) run as a DETACHED process beside a twin session, with
`source=twin-tcp` (brg-0, TcpDevicePort): it reads the session's raw USART tcp port, decodes the frames and pushes them to a Polari backend over gRPC,
and carries the backend's commands back down. The worker does not generate anything: the caller sends the generated project (path -> text).

  POST   /bridges        {session_id, twin_host, twin_port, bridge_name, files, grpc_target?, token?, ttl_s?, owner?}
                         -> {ok, id, state, ...}. `files` = the generated project ({path: text}; only pom.xml, src/** and bridge.properties are used).
                         The jar is BUILT here (mvn, offline from the image's warmed repository) and CACHED by the sha256 of the project (everything
                         but bridge.properties), so the second bridge of the same code starts at once. state: building -> running | failed -> ended.
                         `grpc_target` unset = log-only (grpc.enabled=false): the bridge decodes and prints every frame - the proof mode.
                         `token` is accepted and NOT used: only the twin's control port is token-gated, the USART port the bridge reads is raw.
  GET    /bridges        {ok, bridges: [..], max}
  GET    /bridges/{id}   one bridge + the tail of its output + RSS / CPU seconds of the live process
  GET    /bridges/{id}/logs?tail=N&which=run|build     the output lines
  DELETE /bridges/{id}   stop it now

State is one file per bridge under BRIDGES_DIR (/srv/bridges): <id>.json, <id>.properties, <id>.log (the bridge's stdout+stderr), <id>.build.log. A
reaper kills bridges past their TTL on every request and every REAP_EVERY_S seconds. Jars live in JARS_DIR (/srv/jars) as <sha16>.jar.
"""
import hashlib
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

DIR = os.environ.get('BRIDGES_DIR', '/srv/bridges')
JARS = os.environ.get('JARS_DIR', '/srv/jars')
BUILDS = os.environ.get('BUILDS_DIR', '/srv/builds')
MVN = os.environ.get('BRIDGE_MVN', 'mvn')
JAVA = os.environ.get('BRIDGE_JAVA', 'java')
MAX_BRIDGES = int(os.environ.get('BRIDGES_MAX', '6'))
TTL_DEFAULT, TTL_MAX, TTL_MIN = 1800, 14400, 10
KEEP_DEAD_S = 300
REAP_EVERY_S = 5
MAX_FILES, MAX_PROJECT_BYTES = 400, 8 * 1024 * 1024
BUILD_TIMEOUT_S = 900
JAR_NAME = 'polari-hw-bridge.jar'
_lock = threading.RLock()
_procs = {}        # id -> Popen (a finished child is wait()ed, not left a zombie)
_build_locks = {}  # fingerprint -> Lock (two bridges of the same code build once)
_OWN_KEYS = ('source', 'twin.host', 'twin.port', 'twin.connect_timeout_s', 'grpc.enabled', 'grpc.target', 'max_frames')


def _path(bid, ext):
    return os.path.join(DIR, '%s.%s' % (bid, ext))


def _load(bid):
    try:
        return json.load(open(_path(bid, 'json')))
    except Exception:
        return None


def _save(s):
    p = _path(s['id'], 'json')
    with open(p + '.tmp', 'w') as f:
        json.dump(s, f)
    os.replace(p + '.tmp', p)


def _all():
    os.makedirs(DIR, exist_ok=True)
    out = []
    for fn in sorted(os.listdir(DIR)):
        if fn.endswith('.json'):
            s = _load(fn[:-5])
            if s:
                out.append(s)
    return out


def _alive(s):
    p = _procs.get(s['id'])
    if p is not None:
        return p.poll() is None
    try:
        os.kill(s['pid'], 0)
        return '.jar' in open('/proc/%d/cmdline' % s['pid']).read()
    except Exception:
        return False


def _kill(s):
    p = _procs.get(s['id'])
    pid = s.get('pid')
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except Exception:
            pass
        for _ in range(60):
            if not _alive(s):
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass
            time.sleep(0.1)
    if p is not None:
        try:
            p.wait(timeout=2)
        except Exception:
            pass
    _procs.pop(s['id'], None)


def _remove(bid):
    for ext in ('json', 'json.tmp', 'properties', 'log', 'build.log'):
        try:
            os.remove(_path(bid, ext))
        except OSError:
            pass


def _tail(bid, which='run', n=40):
    try:
        with open(_path(bid, 'build.log' if which == 'build' else 'log'), 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            return f.read().decode('utf-8', 'replace').splitlines()[-n:]
    except Exception:
        return []


def _proc_cost(pid):
    """(rss_kb, cpu_s) of a live process from /proc, or (None, None)."""
    try:
        rss = next(int(l.split()[1]) for l in open('/proc/%d/status' % pid) if l.startswith('VmRSS:'))
        f = open('/proc/%d/stat' % pid).read().rsplit(')', 1)[1].split()
        return rss, round((int(f[11]) + int(f[12])) / float(os.sysconf('SC_CLK_TCK')), 2)
    except Exception:
        return None, None


def reap():
    now = time.time()
    with _lock:
        for s in _all():
            if s['state'] in ('ended', 'failed'):
                if now - s.get('ended', now) > KEEP_DEAD_S:
                    _remove(s['id'])
            elif s['state'] == 'running':
                if not _alive(s):
                    s['state'], s['ended'] = 'ended', now
                    s['end_reason'] = 'the bridge process exited: %s' % ' | '.join(_tail(s['id'], n=2))[-300:]
                    _save(s)
                elif now >= s['expires']:
                    _kill(s)
                    s['state'], s['ended'], s['end_reason'] = 'ended', now, 'TTL reached (%ds): reaped' % s['ttl_s']
                    _save(s)


def _public(s, detail=False):
    now = time.time()
    keys = ('id', 'session_id', 'bridge_name', 'twin_host', 'twin_port', 'grpc_target', 'owner', 'state', 'started', 'expires', 'ttl_s', 'pid', 'jar_sha256',
            'jar_bytes', 'build_fp', 'build', 'end_reason')
    out = {k: s.get(k) for k in keys if k in s}
    out['grpc'] = bool(s.get('grpc_target'))
    out['remaining_s'] = max(0, round(s['expires'] - now, 1)) if s['state'] in ('building', 'running') else 0
    if detail:
        out['output_tail'] = _tail(s['id'], n=15)
        if s['state'] == 'building':
            out['build_tail'] = _tail(s['id'], 'build', 5)
        if s['state'] == 'running':
            out['rss_kb'], out['cpu_s'] = _proc_cost(s['pid'])
    return out


# ------------------------------------------------------------------ the build
def _safe_files(files):
    """{path: text} of the build inputs, or (None, why)."""
    if not isinstance(files, dict) or 'pom.xml' not in files:
        return None, 'POST /bridges needs "files": the generated project as {path: text} (with pom.xml)'
    keep, total = {}, 0
    for path, text in files.items():
        if not isinstance(path, str) or not isinstance(text, str):
            return None, 'files maps path -> text'
        if not (path in ('pom.xml', 'bridge.properties') or path.startswith('src/')):
            continue   # systemd/, install-ubuntu.sh, README.md: not build inputs
        if path.startswith('/') or '..' in path.split('/') or '\0' in path:
            return None, 'file path %r must be relative, inside the project' % path
        total += len(text.encode('utf-8'))
        keep[path] = text
    if len(keep) > MAX_FILES or total > MAX_PROJECT_BYTES:
        return None, 'the project is past %d files / %d bytes' % (MAX_FILES, MAX_PROJECT_BYTES)
    return keep, ''


def _fingerprint(files):
    code = {p: t for p, t in files.items() if p != 'bridge.properties'}
    return hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()[:16]


def _build(fp, files, log_path):
    """-> (jar path | None, why, {'cached', 'wall_s'}). Cached by fingerprint; offline from the warmed repository first, online as the fallback."""
    os.makedirs(JARS, exist_ok=True)
    jar = os.path.join(JARS, '%s.jar' % fp)
    with _lock:
        lk = _build_locks.setdefault(fp, threading.Lock())
    with lk:
        if os.path.isfile(jar):
            return jar, '', {'cached': True, 'wall_s': 0.0}
        t0 = time.time()
        proj = os.path.join(BUILDS, fp)
        shutil.rmtree(proj, ignore_errors=True)
        for path, text in files.items():
            if path == 'bridge.properties':
                continue
            dest = os.path.join(proj, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'w') as f:
                f.write(text)
        try:
            with open(log_path, 'ab') as log:
                for argv in ([MVN, '-B', '-q', '-o', 'package', '-DskipTests'], [MVN, '-B', '-q', 'package', '-DskipTests']):
                    log.write(('$ %s\n' % ' '.join(argv)).encode())
                    log.flush()
                    r = subprocess.run(argv, cwd=proj, stdout=log, stderr=subprocess.STDOUT, timeout=BUILD_TIMEOUT_S)
                    if r.returncode == 0 and os.path.isfile(os.path.join(proj, 'target', JAR_NAME)):
                        break
                else:
                    return None, 'mvn package failed (see the build log)', {'cached': False, 'wall_s': round(time.time() - t0, 1)}
            shutil.copyfile(os.path.join(proj, 'target', JAR_NAME), jar + '.tmp')
            os.replace(jar + '.tmp', jar)
        except subprocess.TimeoutExpired:
            return None, 'mvn package did not finish in %ds' % BUILD_TIMEOUT_S, {'cached': False, 'wall_s': round(time.time() - t0, 1)}
        finally:
            shutil.rmtree(proj, ignore_errors=True)
        return jar, '', {'cached': False, 'wall_s': round(time.time() - t0, 1)}


def _properties(base, s):
    """bridge.properties text: the generated one with this bridge's own keys replaced (twin-tcp, the session's host and port, gRPC on/off)."""
    keep = [ln for ln in (base or '').splitlines() if ln.split('=', 1)[0].strip() not in _OWN_KEYS]
    mine = ['# hosted bridge %s beside twin session %s (sim-2)' % (s['id'], s['session_id']), 'source=twin-tcp', 'twin.host=%s' % s['twin_host'],
            'twin.port=%d' % s['twin_port'], 'twin.connect_timeout_s=30', 'max_frames=0']
    mine += ['grpc.enabled=true', 'grpc.target=%s' % s['grpc_target']] if s.get('grpc_target') else ['grpc.enabled=false']
    return '\n'.join(keep + mine) + '\n'


def _run_bridge(s, files):
    """Build (or find) the jar, then start the detached process. Runs in a thread; every outcome lands in the state file."""
    bid = s['id']
    jar, why, info = _build(s['build_fp'], files, _path(bid, 'build.log'))
    with _lock:
        s = _load(bid)
        if not s or s['state'] != 'building':
            return   # stopped while building
        s['build'] = info
        if not jar:
            s['state'], s['ended'], s['end_reason'] = 'failed', time.time(), why
            _save(s)
            return
        props = _path(bid, 'properties')
        with open(props, 'w') as f:
            f.write(_properties(files.get('bridge.properties', ''), s))
        out = open(_path(bid, 'log'), 'wb')
        proc = subprocess.Popen([JAVA, '-Xmx160m', '-XX:+UseSerialGC', '-Xss512k', '-jar', jar, props], stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, cwd=DIR, start_new_session=True)
        out.close()
        _procs[bid] = proc
        s['state'], s['pid'] = 'running', proc.pid
        s['jar_sha256'] = hashlib.sha256(open(jar, 'rb').read()).hexdigest()
        s['jar_bytes'] = os.path.getsize(jar)
        _save(s)


def start(body):
    """Returns (status, payload)."""
    reap()
    with _lock:
        if not isinstance(body, dict):
            return falcon.HTTP_400, {'ok': False, 'error': 'POST /bridges takes a JSON object'}
        files, why = _safe_files(body.get('files'))
        if files is None:
            return falcon.HTTP_400, {'ok': False, 'error': why}
        host = str(body.get('twin_host') or '')
        if not re.fullmatch(r'[A-Za-z0-9._:-]{1,253}', host):
            return falcon.HTTP_400, {'ok': False, 'error': 'twin_host is the host name or address of the worker running the twin session'}
        try:
            port, ttl = int(body.get('twin_port')), int(body.get('ttl_s') or TTL_DEFAULT)
        except (TypeError, ValueError):
            return falcon.HTTP_400, {'ok': False, 'error': 'twin_port and ttl_s are whole numbers'}
        if not 1 <= port <= 65535:
            return falcon.HTTP_400, {'ok': False, 'error': 'twin_port must be 1 to 65535 (the session\'s USART tcp port)'}
        if not TTL_MIN <= ttl <= TTL_MAX:
            return falcon.HTTP_400, {'ok': False, 'error': 'ttl_s must be %d to %d seconds (%d is the default)' % (TTL_MIN, TTL_MAX, TTL_DEFAULT)}
        grpc = str(body.get('grpc_target') or '')
        if grpc and not re.fullmatch(r'[A-Za-z0-9._\[\]:-]{1,253}:\d{1,5}', grpc):
            return falcon.HTTP_400, {'ok': False, 'error': 'grpc_target is <host>:<port> of the Polari backend\'s gRPC bridge port (unset = log-only)'}
        bname = re.sub(r'[^A-Za-z0-9_.-]', '', str(body.get('bridge_name') or 'bridge'))[:60] or 'bridge'
        session = re.sub(r'[^A-Za-z0-9_.@:-]', '', str(body.get('session_id') or ''))[:80]
        owner = re.sub(r'[^A-Za-z0-9_.@:-]', '', str(body.get('owner') or ''))[:80]
        live = [x for x in _all() if x['state'] in ('building', 'running')]
        if len(live) >= MAX_BRIDGES:
            return falcon.HTTP_503, {'ok': False, 'error': 'this worker already hosts %d bridges (the limit; stop one first)' % len(live)}
        bid, started = secrets.token_hex(6), time.time()
        s = {'id': bid, 'session_id': session, 'bridge_name': bname, 'twin_host': host, 'twin_port': port, 'grpc_target': grpc, 'owner': owner, 'state': 'building',
             'started': started, 'ttl_s': ttl, 'expires': started + ttl, 'pid': 0, 'build_fp': _fingerprint(files)}
        os.makedirs(DIR, exist_ok=True)
        _save(s)
        threading.Thread(target=_run_bridge, args=(s, files), daemon=True, name='bridge-build-' + bid).start()
    # a cached jar starts within a moment: give it a short window so the common answer is already `running`
    for _ in range(40):
        time.sleep(0.05)
        cur = _load(bid)
        if cur and cur['state'] != 'building':
            break
    return falcon.HTTP_200, dict(_public(_load(bid) or s, detail=True), ok=True)


class Bridges:
    def on_get(self, req, resp):
        reap()
        resp.media = {'ok': True, 'bridges': [_public(s) for s in _all()], 'max': MAX_BRIDGES, 'ttl_default_s': TTL_DEFAULT, 'ttl_max_s': TTL_MAX}

    def on_post(self, req, resp):
        try:
            body = json.load(req.bounded_stream)
        except Exception:
            body = None
        resp.status, resp.media = start(body)


def _one(sid):
    return _load(re.sub(r'[^0-9a-f]', '', sid))


class Bridge:
    def on_get(self, req, resp, sid):
        reap()
        s = _one(sid)
        if not s:
            resp.status, resp.media = falcon.HTTP_404, {'ok': False, 'error': 'no bridge %s on this worker (ended long ago, or never started here)' % sid}
            return
        resp.media = dict(_public(s, detail=True), ok=True)

    def on_delete(self, req, resp, sid):
        with _lock:
            s = _one(sid)
            if not s:
                resp.status, resp.media = falcon.HTTP_404, {'ok': False, 'error': 'no bridge %s on this worker' % sid}
                return
            if s['state'] in ('building', 'running'):
                s['state'], s['ended'], s['end_reason'] = 'ended', time.time(), 'stopped'
                _save(s)
                _kill(s)
            _remove(s['id'])
            resp.media = {'ok': True, 'id': s['id'], 'stopped': True}


class Logs:
    def on_get(self, req, resp, sid):
        s = _one(sid)
        if not s:
            resp.status, resp.media = falcon.HTTP_404, {'ok': False, 'error': 'no bridge %s on this worker' % sid}
            return
        try:
            n = max(1, min(int(req.params.get('tail', 100)), 2000))
        except ValueError:
            resp.status, resp.media = falcon.HTTP_400, {'ok': False, 'error': 'tail is a whole number of lines'}
            return
        which = 'build' if req.params.get('which') == 'build' else 'run'
        resp.media = {'ok': True, 'id': s['id'], 'which': which, 'state': s['state'], 'lines': _tail(s['id'], which, n)}


class Reaper:
    """falcon middleware: every request first reaps expired bridges."""
    def process_request(self, req, resp):
        try:
            reap()
        except Exception:
            pass


def info():
    """The /capability block."""
    live = [s for s in _all() if s['state'] in ('building', 'running')]
    return {'available': bool(shutil.which(JAVA) and shutil.which(MVN)), 'max': MAX_BRIDGES, 'active': len(live), 'ttl_default_s': TTL_DEFAULT,
            'ttl_max_s': TTL_MAX, 'cached_jars': len([f for f in os.listdir(JARS) if f.endswith('.jar')]) if os.path.isdir(JARS) else 0}


def _loop():
    while True:
        time.sleep(REAP_EVERY_S)
        try:
            reap()
        except Exception:
            pass


def add_routes(app):
    app.add_route('/bridges', Bridges())
    app.add_route('/bridges/{sid}', Bridge())
    app.add_route('/bridges/{sid}/logs', Logs())
    threading.Thread(target=_loop, daemon=True, name='bridge-reaper').start()
