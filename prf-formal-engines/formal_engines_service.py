"""
formal-engines — the firmware formal + static tier's engines WORKER (sc-2b; the Polari engines pattern of board-engines /
eda-engines): CBMC (bounded model checking) and cppcheck (static rules) behind one JSON API (:9840), so a FormalCheck or a
static scan runs on whatever device the topology assigns (`pol allocate firmwarefaults.formal <instance>`) while the core
keeps the rows. The framework resolves through polari-framework/modules/firmwarefaults/custom/formal_engines.py —
FORMAL_ENGINES_URL knob → a local binary → this image on the local docker → the topology provider → refusal.

  GET  /capability   {worker, engines: {name: {available, version, binary}}, resources (res-2 block from cost.json)}
  GET  /system-info  res-1 shape + the process block
  POST /run          {"engine": name, "args": [..], "files": {relpath: text}, "files_b64": {relpath: b64}, "timeout": s}
                     -> {ok, engine, returncode, stdout, stderr, files, files_b64, cost}
                     ARGV ONLY, no shell; `engine` must be in ENGINES. Unlike the board worker, input files keep a RELATIVE
                     path (stubs/avr/io.h — a model's include tree), never absolute and never with `..`.

sc-2c: + Frama-C 33.0 / Mthread (LGPL-2.1, opam-built in this image): `mthread-check` (polari-mthread-check) and `frama-c`.

CBMC's licence is BSD-4-clause style (an advertising clause): it is GPL-incompatible to LINK, fine as a separate process —
which is all this worker does (plan §2d).
"""
import base64
import json
import os
import resource
import shutil
import subprocess
import tempfile
import time

import falcon

ENGINES = {'cbmc': 'cbmc', 'goto-cc': 'goto-cc', 'goto-instrument': 'goto-instrument', 'cbmc-check': 'polari-cbmc-check',
           'cppcheck': 'cppcheck', 'cppcheck-run': 'polari-cppcheck-run', 'mthread-check': 'polari-mthread-check', 'frama-c': 'frama-c'}
WORKER = 'formal-engines'
VERSION_ARGS = {'cbmc-check': None, 'cppcheck-run': None, 'mthread-check': None, 'frama-c': ['-version']}
#: our wrappers → the engine they drive (its version is what identifies the run)
DRIVES = {'cbmc-check': ('cbmc', 'dpkg'), 'cppcheck-run': ('cppcheck', 'dpkg'), 'mthread-check': ('frama-c', 'cli')}
#: the ceiling for ONE /run's outputs — files + stdout + stderr together (WORKER_MAX_MB, default 32 MB); stdout/stderr come
#: back WHOLE with their lengths (stdout_chars / stderr_chars — the client refuses a cut stream), past the ceiling = 413
MAX_BYTES = int(os.environ.get('WORKER_MAX_MB', '32')) * 1024 * 1024


def _version(engine):
    b = shutil.which(ENGINES[engine])
    if not b:
        return ''
    if VERSION_ARGS.get(engine, ['--version']) is None:
        tool, how = DRIVES.get(engine, ('?', 'dpkg'))
        ver = _dpkg(tool) if how == 'dpkg' else _cli_version(tool)
        return 'ours (in this image) — drives %s %s' % (tool, ver)
    try:
        r = subprocess.run([b] + VERSION_ARGS.get(engine, ['--version']), capture_output=True, text=True, timeout=20)
        out = (r.stdout or r.stderr or '').strip().splitlines()
        return out[0][:160] if out else 'present'
    except Exception:
        return 'present'


def _cli_version(tool):
    try:
        return subprocess.run([tool, '-version'], capture_output=True, text=True, timeout=60).stdout.strip()[:80]
    except Exception:
        return ''


def _dpkg(pkg):
    try:
        return subprocess.run(['dpkg-query', '-W', '-f', '${Version}', pkg], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ''


def _resources_block():
    try:
        measured = json.load(open('/srv/cost.json'))
    except Exception:
        measured = {}
    return dict({'ramMb': 256, 'minThreads': 1, 'threadCeiling': 1, 'cpuBenefit': 'none', 'fidelity': 'declared',
                 'note': 'res-2 block (sc-2b): one CBMC check at a time; measured values in the firmwarefaults module COST.md'}, **measured)


class Capability:
    def on_get(self, req, resp):
        resp.media = {'worker': WORKER, 'resources': _resources_block(),
                      'engines': {e: {'available': bool(shutil.which(b)), 'version': _version(e), 'binary': b} for e, b in ENGINES.items()},
                      'protocol': 'POST /run {engine, args, files, files_b64, timeout} — argv only; files keep relative paths (no ..)'}


class SystemInfo:
    def on_get(self, req, resp):
        import platform
        info = {}
        try:
            for line in open('/proc/meminfo'):
                k, v = line.split(':', 1)
                if k in ('MemTotal', 'MemAvailable'):
                    info[k] = int(v.split()[0]) * 1024
        except Exception:
            pass
        resp.media = {'ok': True, 'worker': WORKER, 'cpus': os.cpu_count(), 'memTotalBytes': info.get('MemTotal', 0),
                      'memAvailableBytes': info.get('MemAvailable', 0), 'platform': platform.platform()}


def _bad(resp, msg, status=falcon.HTTP_400):
    resp.status = status
    resp.media = {'ok': False, 'error': msg}


def safe_rel(name):
    """A relative path inside the job dir, or None (absolute, `..`, empty)."""
    n = os.path.normpath(str(name))
    if not n or n.startswith('/') or n.startswith('..') or '/../' in n or n == '.':
        return None
    return n


class Run:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        engine = str(body.get('engine', ''))
        if engine not in ENGINES:
            return _bad(resp, 'unknown engine %r — one of %s' % (engine, sorted(ENGINES)))
        binary = shutil.which(ENGINES[engine])
        if not binary:
            return _bad(resp, '%s absent in this worker' % engine, falcon.HTTP_503)
        args = [str(a) for a in (body.get('args') or [])]
        if any('..' in a or a.startswith('/') for a in args):
            return _bad(resp, 'args must be relative paths inside the job: %s' % args)
        timeout = min(float(body.get('timeout', 300) or 300), 1800.0)
        work = tempfile.mkdtemp(prefix='formal-')
        try:
            sent = set()
            for kind, src in (('t', body.get('files') or {}), ('b', body.get('files_b64') or {})):
                for name, data in src.items():
                    rel = safe_rel(name)
                    if rel is None:
                        return _bad(resp, 'bad file path %r' % name)
                    fp = os.path.join(work, rel)
                    os.makedirs(os.path.dirname(fp), exist_ok=True)
                    open(fp, 'wb').write(data.encode() if kind == 't' else base64.b64decode(data))
                    sent.add(rel)
            c0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            t0 = time.perf_counter()
            try:
                run = subprocess.run([binary] + args, capture_output=True, text=True, timeout=timeout, cwd=work, env=dict(os.environ, HOME=work))
            except subprocess.TimeoutExpired:
                return _bad(resp, '%s exceeded %.0fs' % (engine, timeout), falcon.HTTP_504)
            c1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            files, files_b64 = {}, {}
            total = len(run.stdout.encode('utf-8', 'replace')) + len(run.stderr.encode('utf-8', 'replace'))
            if total > MAX_BYTES:
                return _bad(resp, '%s wrote %d bytes to stdout/stderr — past WORKER_MAX_MB (%d B); refused, never cut' % (engine, total, MAX_BYTES), falcon.HTTP_413)
            for fn in sorted(os.listdir(work)):
                fp = os.path.join(work, fn)
                if fn in sent or not os.path.isfile(fp) or fn.endswith('.gb'):
                    continue
                data = open(fp, 'rb').read()
                total += len(data)
                if total > MAX_BYTES:
                    return _bad(resp, 'outputs (files + stdout + stderr) exceed WORKER_MAX_MB (%d B)' % MAX_BYTES, falcon.HTTP_413)
                if b'\0' in data[:4096]:
                    files_b64[fn] = base64.b64encode(data).decode()
                else:
                    files[fn] = data.decode('utf-8', 'replace')
            cost = {'wall_s': round(time.perf_counter() - t0, 3), 'cpu_s': round((c1.ru_utime - c0.ru_utime) + (c1.ru_stime - c0.ru_stime), 3),
                    'peak_rss_mb': round(c1.ru_maxrss / 1024.0, 1), 'source': 'worker rusage(RUSAGE_CHILDREN) around the engine'}
            resp.media = {'ok': True, 'engine': engine, 'returncode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr,
                          'stdout_chars': len(run.stdout), 'stderr_chars': len(run.stderr), 'max_bytes': MAX_BYTES,
                          'files': files, 'files_b64': files_b64, 'cost': cost}
        finally:
            shutil.rmtree(work, ignore_errors=True)


app = falcon.App()
app.add_route('/capability', Capability())
app.add_route('/system-info', SystemInfo())
app.add_route('/run', Run())
