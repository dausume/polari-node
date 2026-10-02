"""
board-engines — the board arc's engines WORKER (brd-1; the Polari engines pattern of eda-engines / torch-engines):
the AVR toolchain, avrdude and the simavr twin harness behind one JSON API (:9830), so `pol board build` compiles on
whatever device the topology assigns (`pol allocate board.engines <instance>`) while the core keeps the rows. The
framework resolves through polari-framework/modules/board/custom/board_engines.py — BOARD_ENGINES_URL knob → a local
binary → this image on the local docker → the topology provider → refusal.

  GET  /capability   {worker, engines: {name: {available, version, binary}}, resources (res-2 block)}
  GET  /system-info  res-1 shape + the res-3 process block
  POST /run          {"engine": name, "args": [..], "files": {name: text}, "files_b64": {name: b64}, "timeout": s}
                     -> {ok, engine, returncode, stdout, stderr, files, files_b64, cost}
                     ARGV ONLY, no shell; `engine` must be in ENGINES; args are basenames inside the job dir.

A FLASH never runs through this API (the USB port is on the host that holds the board — plan §2; the framework
refuses a remote flash before it gets here). The twin needs a long-lived TCP port, so it runs as its own container
of this image (`pol board twin uno up`), not through /run.
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

ENGINES = {'avr-gcc': 'avr-gcc', 'avr-objcopy': 'avr-objcopy', 'avr-size': 'avr-size', 'avrdude': 'avrdude',
           'simavr': 'simavr', 'avr-twin': 'polari-avr-twin',
           # sc-0 (firmwarefaults): the disassembly + symbols a scenario resolves its PCs against, and the VCD reader
           'avr-objdump': 'avr-objdump', 'avr-nm': 'avr-nm', 'vcd-window': 'polari-vcd-window'}
VERSION_ARGS = {'avrdude': ['-?'], 'simavr': ['--list-cores'], 'avr-twin': None}
#: the ceiling for ONE /run's outputs — files + stdout + stderr together (WORKER_MAX_MB, default 16 MB). Until sc-2 the
#: worker kept only the last 20 000 characters of stdout/stderr, so `avr-objdump -d` of the UNO firmware (≈ 87 kB) lost
#: its start (hal_millis) through the worker while the local image saw it all. Now stdout/stderr come back WHOLE, their
#: lengths are stated (stdout_chars / stderr_chars, the client checks them), and a run past the ceiling is REFUSED (413) —
#: never silently cut.
MAX_BYTES = int(os.environ.get('WORKER_MAX_MB', '16')) * 1024 * 1024


def _version(engine):
    b = shutil.which(ENGINES[engine])
    if not b:
        return ''
    args = VERSION_ARGS.get(engine, ['--version'])
    if args is None:
        return 'built in this image against libsimavr (dpkg: %s)' % _dpkg('libsimavr2')
    if engine in ('avrdude', 'simavr'):
        return 'dpkg %s' % _dpkg(engine)
    try:
        r = subprocess.run([b] + args, capture_output=True, text=True, timeout=20)
        out = (r.stdout or r.stderr or '').strip().splitlines()
        return out[0][:160] if out else 'present'
    except Exception:
        return 'present'


def _dpkg(pkg):
    try:
        return subprocess.run(['dpkg-query', '-W', '-f', '${Version}', pkg], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ''


def _process_block():
    out = {}
    try:
        for line in open('/proc/self/status'):
            if line.startswith('VmRSS:'):
                out['residentMb'] = round(int(line.split()[1]) / 1024.0, 1)
            elif line.startswith('VmHWM:'):
                out['peakMb'] = round(int(line.split()[1]) / 1024.0, 1)
    except Exception:
        pass
    return out


def _resources_block():
    try:
        measured = json.load(open('/srv/cost.json'))
    except Exception:
        measured = {}
    return dict({'ramMb': 64, 'minThreads': 1, 'threadCeiling': 1, 'cpuBenefit': 'none', 'fidelity': 'declared',
                 'note': 'res-2 block (brd-1): one avr-gcc compile at a time; measured values in the board module COST.md'}, **measured)


class Capability:
    def on_get(self, req, resp):
        resp.media = {'worker': 'board-engines', 'resources': _resources_block(),
                      'engines': {e: {'available': bool(shutil.which(b)), 'version': _version(e), 'binary': b} for e, b in ENGINES.items()},
                      'protocol': 'POST /run {engine, args, files, files_b64, timeout} — argv only, files round-trip; never a flash'}


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
        resp.media = {'ok': True, 'worker': 'board-engines', 'process': _process_block(), 'cpus': os.cpu_count(),
                      'memTotalBytes': info.get('MemTotal', 0), 'memAvailableBytes': info.get('MemAvailable', 0), 'platform': platform.platform()}


def _bad(resp, msg, status=falcon.HTTP_400):
    resp.status = status
    resp.media = {'ok': False, 'error': msg}


class Run:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        engine = str(body.get('engine', ''))
        if engine not in ENGINES:
            return _bad(resp, 'unknown engine %r — one of %s' % (engine, sorted(ENGINES)))
        if engine == 'avrdude':
            return _bad(resp, 'a flash never runs on a worker: the USB port is on the host holding the board (plan §2)', falcon.HTTP_403)
        binary = shutil.which(ENGINES[engine])
        if not binary:
            return _bad(resp, '%s absent in this worker' % engine, falcon.HTTP_503)
        args = [str(a) for a in (body.get('args') or [])]
        if any('..' in a or (a.startswith('/') and not a.startswith('/usr/')) for a in args):
            return _bad(resp, 'args must be basenames inside the job (or /usr/…): %s' % args)
        timeout = min(float(body.get('timeout', 300) or 300), 1800.0)
        work = tempfile.mkdtemp(prefix='board-')
        try:
            sent = set()
            for name, text in (body.get('files') or {}).items():
                fn = os.path.basename(name); sent.add(fn)
                open(os.path.join(work, fn), 'w').write(text)
            for name, b64 in (body.get('files_b64') or {}).items():
                fn = os.path.basename(name); sent.add(fn)
                open(os.path.join(work, fn), 'wb').write(base64.b64decode(b64))
            c0 = resource.getrusage(resource.RUSAGE_CHILDREN); t0 = time.perf_counter()
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
                if fn in sent or not os.path.isfile(fp):
                    continue
                data = open(fp, 'rb').read(); total += len(data)
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
