"""
pcb-engines — the PCB arc's engines WORKER (pcb-0, AI-Notes/plans/PCB_FROM_SCRATCH_PLAN.md §6; the Polari engines pattern of
board-engines / formal-engines): KiCad's headless `kicad-cli` behind one JSON API (:9860), so ERC, DRC and the fabrication
exports run on whatever device the topology assigns (`pol allocate pcb.engines <instance>`) while the core keeps the rows.
The framework resolves through polari-framework/modules/pcb/custom/pcb_engines.py — PCB_ENGINES_URL knob → a local
kicad-cli → this image on the local docker → the topology provider `pcb.engines` → refusal naming the knob.

  GET  /capability   {worker, engines: {kicad-cli: {available, version, binary}}, libraries: {kicad-symbols: {version,
                      md5sums_sha256, files}, kicad-footprints: …}, verbs, resources (res-2 block from cost.json)}
  GET  /system-info  res-1 shape + the process block
  GET  /library?kind=symbol|footprint&lib=Device&name=R
                     -> {ok, kind, lib, name, text, lib_file, lib_sha256, lib_version}: ONE official library entry as KiCad ships it
                     (a top-level symbol cut from <lib>.kicad_sym, or the whole <lib>.pretty/<name>.kicad_mod) + the sha256 of the
                     library FILE it came from — Polari writes .kicad_sch files directly (D-pcb-1) and embeds these (lib_symbols);
                     a DERIVED symbol ((extends …)) is refused (flattening is not done here). Read-only; names are checked.
  POST /run          {"engine": "kicad-cli", "args": [..], "files": {relpath: text}, "files_b64": {relpath: b64}, "timeout": s,
                      "source_date": "YYYY-MM-DD HH:MM:SS"}
                     -> {ok, engine, returncode, stdout, stderr, files, files_b64, cost, source_date, job_dir}
                     ARGV ONLY, no shell; the verb (args[0:2] or args[0:3]) must be in VERBS; input files keep RELATIVE
                     paths (a project's footprints.pretty/…), never absolute, never `..`. Outputs = every file the run
                     created under the job dir, at its relative path (gerbers/board-F_Cu.gtl), the inputs excluded.

REPRODUCIBLE BYTES: kicad-cli writes the wall clock into Gerbers (%TF.CreationDate, the G04 line), drill files, the job file,
SVG titles, the STEP header and netlists, and ignores SOURCE_DATE_EPOCH. With `source_date` the run goes under libfaketime
with the clock FROZEN there (FAKETIME absolute; the monotonic clock left real so KiCad's own waits still advance); the job
always runs in the SAME directory (JOB_DIR — the netlist names its source path), one run at a time (gunicorn -w 1, sync).
Without `source_date` the real clock is used and the outputs differ run to run by those stamps only.

KiCad's config (`~/.config/kicad/9.0`) goes to a per-run HOME outside the job dir, so it never comes back as an output and
no state leaks between runs. KiCad is GPL-3.0 and only ever runs here as a separate process.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import falcon

ENGINES = {'kicad-cli': 'kicad-cli'}
WORKER = 'pcb-engines'
#: the headless verbs pcb-0 relays (plan §0 / §7) — nothing that edits a design
VERBS = (('version',), ('sch', 'erc'), ('sch', 'export', 'netlist'), ('sch', 'export', 'bom'), ('sch', 'export', 'svg'),
         ('sch', 'export', 'pdf'), ('pcb', 'drc'), ('pcb', 'export', 'gerbers'), ('pcb', 'export', 'drill'),
         ('pcb', 'export', 'pos'), ('pcb', 'export', 'svg'), ('pcb', 'export', 'step'))
LIBRARIES = ('kicad-symbols', 'kicad-footprints')
MAX_BYTES = int(os.environ.get('WORKER_MAX_MB', '48')) * 1024 * 1024
JOB_DIR = '/tmp/pcb-job'
FAKETIME_LIB = '/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1'
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


def _dpkg(pkg):
    try:
        return subprocess.run(['dpkg-query', '-W', '-f', '${Version}', pkg], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ''


def _version():
    b = shutil.which('kicad-cli')
    if not b:
        return ''
    try:
        r = subprocess.run([b, 'version'], capture_output=True, text=True, timeout=60, env=dict(os.environ, HOME=tempfile.gettempdir()))
        return ('kicad-cli %s (dpkg kicad %s)' % ((r.stdout or '').strip(), _dpkg('kicad')))[:160]
    except Exception:
        return 'present'


def _library(pkg):
    """The library's identity: the dpkg version + the sha256 of dpkg's own md5sums list (every file's md5 → one content
    fingerprint, cheap: no walk of 370 MB)."""
    p = '/var/lib/dpkg/info/%s.md5sums' % pkg
    try:
        data = open(p, 'rb').read()
        return {'version': _dpkg(pkg), 'md5sums_sha256': hashlib.sha256(data).hexdigest(), 'files': data.count(b'\n')}
    except Exception:
        return {'version': '', 'md5sums_sha256': '', 'files': 0}


def _resources_block():
    try:
        measured = json.load(open('/srv/cost.json'))
    except Exception:
        measured = {}
    return dict({'ramMb': 512, 'minThreads': 1, 'threadCeiling': 1, 'cpuBenefit': 'none', 'fidelity': 'declared',
                 'note': 'res-2 block (pcb-0): one kicad-cli run at a time; measured values in the pcb module COST.md'}, **measured)


class Capability:
    def on_get(self, req, resp):
        b = shutil.which('kicad-cli')
        resp.media = {'worker': WORKER, 'resources': _resources_block(),
                      'engines': {'kicad-cli': {'available': bool(b), 'version': _version(), 'binary': 'kicad-cli'}},
                      'libraries': {p: _library(p) for p in LIBRARIES},
                      'verbs': [' '.join(v) for v in VERBS], 'reproducible': {'source_date': os.path.exists(FAKETIME_LIB), 'faketime': _dpkg('libfaketime'),
                                                                  'job_dir': JOB_DIR}, 'display': 'none (no X, no Xvfb — kicad-cli is headless)',
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


_NAME_RE = re.compile(r'^[A-Za-z0-9_.+\-]+$')
SYMBOL_DIR = '/usr/share/kicad/symbols'
FOOTPRINT_DIR = '/usr/share/kicad/footprints'


def cut_symbol(text, name):
    """The top-level (symbol "name" …) block of a .kicad_sym, by paren matching (strings respected), or None."""
    i = text.find('\n\t(symbol "%s"' % name)
    if i < 0:
        return None
    i += 2
    depth, j, n, instr = 0, i, len(text), False
    while j < n:
        c = text[j]
        if instr:
            if c == '\\':
                j += 1
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1
    return None


class Library:
    def on_get(self, req, resp):
        kind, lib, name = req.get_param('kind') or '', req.get_param('lib') or '', req.get_param('name') or ''
        if kind not in ('symbol', 'footprint') or not _NAME_RE.match(lib) or not _NAME_RE.match(name):
            return _bad(resp, 'kind must be symbol|footprint and lib/name plain library names (got %r %r %r)' % (kind, lib, name))
        path = (os.path.join(SYMBOL_DIR, '%s.kicad_sym' % lib) if kind == 'symbol'
                else os.path.join(FOOTPRINT_DIR, '%s.pretty' % lib, '%s.kicad_mod' % name))
        if not os.path.isfile(path):
            return _bad(resp, 'no %s %s:%s in the installed libraries (%s)' % (kind, lib, name, path), falcon.HTTP_404)
        data = open(path, 'rb').read()
        text = data.decode('utf-8')
        if kind == 'symbol':
            text = cut_symbol(text, name)
            if text is None:
                return _bad(resp, 'no symbol %s in %s' % (name, path), falcon.HTTP_404)
            if '(extends ' in text[:400]:
                return _bad(resp, 'symbol %s:%s is DERIVED (extends) — not flattened here; pick its base symbol' % (lib, name), falcon.HTTP_422)
        resp.media = {'ok': True, 'kind': kind, 'lib': lib, 'name': name, 'text': text, 'lib_file': path,
                      'lib_sha256': hashlib.sha256(data).hexdigest(),
                      'lib_version': 'kicad-%ss %s' % (kind, _dpkg('kicad-%ss' % kind))}


def _bad(resp, msg, status=falcon.HTTP_400):
    resp.status = status
    resp.media = {'ok': False, 'error': msg}


def safe_rel(name):
    """A relative path inside the job dir, or None (absolute, `..`, empty)."""
    n = os.path.normpath(str(name))
    if not n or n.startswith('/') or n.startswith('..') or '/../' in n or n == '.':
        return None
    return n


def verb_of(args):
    for v in VERBS:
        if tuple(args[:len(v)]) == v:
            return v
    return None


class _Done:
    pass


def _run_measured(argv, cwd, env, scratch, timeout):
    """Run argv with stdout/stderr to files and reap it with wait4 — the rusage is THIS process's (peak RSS, CPU-s), not
    the worker's lifetime maximum. None on timeout (the process is killed)."""
    out_p, err_p = os.path.join(scratch, '.stdout'), os.path.join(scratch, '.stderr')
    with open(out_p, 'wb') as fo, open(err_p, 'wb') as fe:
        t0 = time.perf_counter()
        p = subprocess.Popen(argv, stdout=fo, stderr=fe, cwd=cwd, env=env)
        while True:
            pid, status, ru = os.wait4(p.pid, os.WNOHANG)
            if pid:
                break
            if time.perf_counter() - t0 > timeout:
                p.kill()
                os.wait4(p.pid, 0)
                p.returncode = -9
                return None
            time.sleep(0.01)
        p.returncode = os.waitstatus_to_exitcode(status)   # recorded so Popen.__del__ never re-reaps
    d = _Done()
    d.wall_s = time.perf_counter() - t0
    d.returncode = p.returncode
    d.rusage = ru
    d.stdout = open(out_p, 'rb').read().decode('utf-8', 'replace')
    d.stderr = open(err_p, 'rb').read().decode('utf-8', 'replace')
    return d


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
        if verb_of(args) is None:
            return _bad(resp, 'verb %r not relayed — one of %s' % (' '.join(args[:3]), [' '.join(v) for v in VERBS]))
        if any('..' in a or a.startswith('/') for a in args):
            return _bad(resp, 'args must be relative paths inside the job: %s' % args)
        timeout = min(float(body.get('timeout', 300) or 300), 1800.0)
        source_date = str(body.get('source_date') or '')
        if source_date and not _DATE_RE.match(source_date):
            return _bad(resp, 'source_date must be "YYYY-MM-DD HH:MM:SS" (got %r)' % source_date)
        if source_date and not os.path.exists(FAKETIME_LIB):
            return _bad(resp, 'source_date asked but libfaketime is absent in this worker (%s)' % FAKETIME_LIB, falcon.HTTP_503)
        shutil.rmtree(JOB_DIR, ignore_errors=True)
        os.makedirs(JOB_DIR)
        work = JOB_DIR
        home = tempfile.mkdtemp(prefix='pcb-home-')
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
            # an output directory named in the args (`-o gerbers/`) must exist for some verbs
            for i, a in enumerate(args[:-1]):
                if a in ('-o', '--output') and args[i + 1].endswith('/'):
                    os.makedirs(os.path.join(work, args[i + 1]), exist_ok=True)
            env = {k: v for k, v in os.environ.items() if k != 'DISPLAY'}
            env['HOME'] = home
            if source_date:
                env.update(LD_PRELOAD=FAKETIME_LIB, FAKETIME=source_date, DONT_FAKE_MONOTONIC='1', FAKETIME_DONT_FAKE_MONOTONIC='1')
            t0 = time.perf_counter()
            run = _run_measured([binary] + args, work, env, home, timeout)
            if run is None:
                return _bad(resp, '%s exceeded %.0fs' % (engine, timeout), falcon.HTTP_504)
            files, files_b64 = {}, {}
            total = len(run.stdout.encode('utf-8', 'replace')) + len(run.stderr.encode('utf-8', 'replace'))
            if total > MAX_BYTES:
                return _bad(resp, 'stdout/stderr past WORKER_MAX_MB — refused, never cut', falcon.HTTP_413)
            for root, dirs, fns in os.walk(work):
                dirs.sort()
                for fn in sorted(fns):
                    fp = os.path.join(root, fn)
                    rel = os.path.relpath(fp, work)
                    if rel in sent or not os.path.isfile(fp):
                        continue
                    data = open(fp, 'rb').read()
                    total += len(data)
                    if total > MAX_BYTES:
                        return _bad(resp, 'outputs (files + stdout + stderr) exceed WORKER_MAX_MB (%d B)' % MAX_BYTES, falcon.HTTP_413)
                    try:
                        if b'\0' in data[:4096]:
                            raise UnicodeDecodeError('bin', b'', 0, 1, 'nul')
                        files[rel] = data.decode('utf-8')
                    except UnicodeDecodeError:
                        files_b64[rel] = base64.b64encode(data).decode()
            ru = run.rusage
            cost = {'wall_s': round(run.wall_s, 3), 'cpu_s': round(ru.ru_utime + ru.ru_stime, 3),
                    'peak_rss_mb': round(ru.ru_maxrss / 1024.0, 1), 'source': 'worker wait4() on the kicad-cli process (its own rusage)'}
            resp.media = {'ok': True, 'engine': engine, 'verb': ' '.join(verb_of(args)), 'returncode': run.returncode,
                          'stdout': run.stdout, 'stderr': run.stderr, 'stdout_chars': len(run.stdout), 'stderr_chars': len(run.stderr),
                          'max_bytes': MAX_BYTES, 'source_date': source_date, 'job_dir': JOB_DIR, 'files': files, 'files_b64': files_b64, 'cost': cost}
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)


app = falcon.App()
app.add_route('/capability', Capability())
app.add_route('/system-info', SystemInfo())
app.add_route('/run', Run())
app.add_route('/library', Library())
