"""
cnt-engines HTTP surface — the worker side of modules/cntfet (dist-1).

Same contract as msci-engines: capability() is honest, every compute
endpoint returns data or an evidence-bearing refusal, JSON only, no
framework import. The backend's cntfet.cnt_remote translates the
module's subprocess primitives (compile_osdi / run_ngspice / run_sta /
kwant worker) into these calls when the engines are not local.

  GET  /capability           engines present + declared resources
  GET  /system-info          host specs (res-1 shape)
  POST /osdi/compile         {"va": text}            -> {osdiId}
  POST /ngspice/run          {"name", "netlist", "osdiId"?, "timeout"?}
                             -> {returncode, stdout, stderr, files}
                             (`pre_osdi <anything>` lines are rewritten
                              to the cached .osdi for osdiId)
  POST /sta/run              {"files": {name: text}, "script": text,
                              "timeout"?}  -> {returncode, stdout, stderr}
                             (script paths are BASENAMES, cwd = workdir)
  POST /kwant/run            {"job": {...}} -> the worker's JSON verbatim
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile

import falcon

OSDI_CACHE = os.environ.get('CNT_OSDI_CACHE', '/var/cache/cnt-osdi')
MAX_FILE_BYTES = 32 * 1024 * 1024
TAIL = 4000


def _which(name):
    return shutil.which(name)


def _version(cmd, args=('--version',), pattern=None):
    try:
        run = subprocess.run([cmd, *args], capture_output=True,
                             text=True, timeout=30)
        text = (run.stdout + run.stderr).strip()
        if pattern:
            m = re.search(pattern, text)
            return m.group(1) if m else text[:80]
        return text.splitlines()[0][:80] if text else ''
    except Exception as exc:
        return f'probe failed: {exc}'


def _kwant_probe():
    py = '/opt/kwant-venv/bin/python'
    if not os.path.isfile(py):
        return {'available': False, 'error': 'kwant venv absent'}
    try:
        run = subprocess.run(
            [py, '-c', 'import kwant, numpy; '
                       'print(kwant.__version__, numpy.__version__)'],
            capture_output=True, text=True, timeout=120)
        if run.returncode == 0:
            kv, nv = run.stdout.split()
            return {'available': True, 'version': kv, 'numpy': nv,
                    'python': py}
        return {'available': False, 'error': run.stderr[-300:]}
    except Exception as exc:
        return {'available': False, 'error': str(exc)}


def _capability():
    ngspice = _which('ngspice')
    openvaf = _which('openvaf')
    sta = _which('sta')
    report = {
        'service': 'cnt-engines',
        'engines': {
            'ngspice': ({'available': True, 'path': ngspice,
                         'version': _version(
                             ngspice, ('-v',),
                             r'ngspice-(\d+)')}
                        if ngspice else {'available': False}),
            'openvaf': ({'available': True, 'path': openvaf,
                         'flavor': 'openvaf 23.5.0 (OSDI 0.3)'}
                        if openvaf else {'available': False}),
            'opensta': ({'available': True, 'path': sta,
                         'version': _version(sta, ('-version',))}
                        if sta else {'available': False}),
            'kwant': _kwant_probe(),
        },
        'osdiCache': {'dir': OSDI_CACHE,
                      'entries': len([f for f in os.listdir(OSDI_CACHE)
                                      if f.endswith('.osdi')])
                      if os.path.isdir(OSDI_CACHE) else 0},
        'resources': {
            'minThreads': 1,
            'threadCeiling': os.cpu_count() or 1,  # one ngspice/kwant
            'cpuBenefit': 'linear',                # process per job
            'ramMb': 1500,   # kwant SCF chains dominate
            'imageMb': 2600,
            'fidelity': 'declared',
        },
    }
    return report


class CapabilityResource:
    def on_get(self, req, resp):
        resp.media = _capability()


class SystemInfoResource:
    """res-1 shape (same as msci-engines)."""

    @staticmethod
    def _meminfo():
        info = {}
        try:
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split()
                    if parts and parts[0].rstrip(':') in (
                            'MemTotal', 'MemAvailable'):
                        info[parts[0].rstrip(':')] = int(parts[1]) * 1024
        except (OSError, ValueError, IndexError):
            pass
        return info

    def on_get(self, req, resp):
        import platform
        mem = self._meminfo()
        total = mem.get('MemTotal', 0)
        avail = mem.get('MemAvailable', 0)
        try:
            usage = shutil.disk_usage('/')
            disk = {'totalBytes': usage.total, 'freeBytes': usage.free}
        except OSError:
            disk = {'totalBytes': 0, 'freeBytes': 0}
        resp.media = [{'system-info': {
            'platform': {'systemType': platform.system(),
                         'networkName': os.environ.get(
                             'HOSTNAME', platform.node()),
                         'arch': platform.machine(),
                         'isContainerized': True,
                         'service': 'cnt-engines'},
            'cpu': {'numLogicalCPUs': os.cpu_count() or 0,
                    'currentUsagePercent': 0},
            'memory': {'total': total, 'available': avail,
                       'percentUsed': round(
                           (total - avail) * 100.0 / total, 1)
                       if total else 0},
            'disk': disk,
        }}]


def _bad(resp, msg, status=falcon.HTTP_400):
    resp.status = status
    resp.media = {'ok': False, 'error': msg}


class OsdiCompileResource:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        va = body.get('va', '')
        if not va:
            return _bad(resp, "'va' (Verilog-A text) is required")
        openvaf = _which('openvaf')
        if not openvaf:
            return _bad(resp, 'openvaf absent in this worker',
                        falcon.HTTP_503)
        osdi_id = hashlib.sha256(va.encode()).hexdigest()[:24]
        os.makedirs(OSDI_CACHE, exist_ok=True)
        target = os.path.join(OSDI_CACHE, f'{osdi_id}.osdi')
        if os.path.isfile(target):
            resp.media = {'ok': True, 'osdiId': osdi_id, 'cached': True,
                          'compiler': 'openvaf 23.5.0 (OSDI 0.3)'}
            return
        work = tempfile.mkdtemp(prefix='osdi-')
        va_path = os.path.join(work, 'model.va')
        with open(va_path, 'w') as fh:
            fh.write(va)
        run = subprocess.run([openvaf, va_path], capture_output=True,
                             text=True, timeout=300, cwd=work)
        built = os.path.join(work, 'model.osdi')
        if run.returncode != 0 or not os.path.isfile(built):
            shutil.rmtree(work, ignore_errors=True)
            return _bad(resp, 'openvaf failed: ' + run.stderr[-2000:],
                        falcon.HTTP_422)
        shutil.move(built, target)
        shutil.rmtree(work, ignore_errors=True)
        resp.media = {'ok': True, 'osdiId': osdi_id, 'cached': False,
                      'compiler': 'openvaf 23.5.0 (OSDI 0.3)'}


_PRE_OSDI = re.compile(r'^(\s*pre_osdi\s+)\S+', re.M)


class NgspiceRunResource:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        netlist = body.get('netlist', '')
        name = os.path.basename(body.get('name', 'run.sp')) or 'run.sp'
        if not netlist:
            return _bad(resp, "'netlist' text is required")
        ngspice = _which('ngspice')
        if not ngspice:
            return _bad(resp, 'ngspice absent in this worker',
                        falcon.HTTP_503)
        osdi_id = body.get('osdiId')
        if osdi_id:
            path = os.path.join(OSDI_CACHE,
                                f'{os.path.basename(osdi_id)}.osdi')
            if not os.path.isfile(path):
                return _bad(resp, f'osdiId {osdi_id} not in the cache — '
                                  f'POST /osdi/compile first',
                            falcon.HTTP_422)
            netlist = _PRE_OSDI.sub(lambda m: m.group(1) + path,
                                    netlist)
        elif 'remote://' in netlist:
            return _bad(resp, 'netlist references remote:// OSDI but '
                              'no osdiId was sent')
        timeout = min(float(body.get('timeout', 600) or 600), 3600.0)
        work = tempfile.mkdtemp(prefix='ngspice-')
        try:
            path = os.path.join(work, name)
            with open(path, 'w') as fh:
                fh.write(netlist)
            try:
                run = subprocess.run([ngspice, '-b', path],
                                     capture_output=True, text=True,
                                     timeout=timeout, cwd=work)
            except subprocess.TimeoutExpired:
                return _bad(resp, f'ngspice exceeded {timeout:.0f}s',
                            falcon.HTTP_504)
            files, total = {}, 0
            for fn in sorted(os.listdir(work)):
                fp = os.path.join(work, fn)
                if fn == name or not os.path.isfile(fp):
                    continue
                size = os.path.getsize(fp)
                total += size
                if total > MAX_FILE_BYTES:
                    return _bad(resp, 'outputs exceed the 32 MB cap',
                                falcon.HTTP_413)
                with open(fp, errors='replace') as fh:
                    files[fn] = fh.read()
            resp.media = {'ok': True, 'returncode': run.returncode,
                          'stdout': run.stdout[-TAIL:],
                          'stderr': run.stderr[-TAIL:],
                          'files': files}
        finally:
            shutil.rmtree(work, ignore_errors=True)


class StaRunResource:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        script = body.get('script', '')
        files = body.get('files', {}) or {}
        if not script:
            return _bad(resp, "'script' (tcl text) is required")
        sta = _which('sta')
        if not sta:
            return _bad(resp, 'OpenSTA absent in this worker',
                        falcon.HTTP_503)
        timeout = min(float(body.get('timeout', 300) or 300), 1800.0)
        work = tempfile.mkdtemp(prefix='sta-')
        try:
            for fn, text in files.items():
                with open(os.path.join(work, os.path.basename(fn)),
                          'w') as fh:
                    fh.write(text)
            tcl = os.path.join(work, 'run.tcl')
            with open(tcl, 'w') as fh:
                fh.write(script)
            try:
                run = subprocess.run([sta, '-no_splash', '-exit', tcl],
                                     capture_output=True, text=True,
                                     timeout=timeout, cwd=work)
            except subprocess.TimeoutExpired:
                return _bad(resp, f'sta exceeded {timeout:.0f}s',
                            falcon.HTTP_504)
            resp.media = {'ok': True, 'returncode': run.returncode,
                          'stdout': run.stdout[-4 * TAIL:],
                          'stderr': run.stderr[-TAIL:]}
        finally:
            shutil.rmtree(work, ignore_errors=True)


class KwantRunResource:
    def on_post(self, req, resp):
        body = json.load(req.bounded_stream)
        job = body.get('job')
        if not isinstance(job, dict):
            return _bad(resp, "'job' object is required")
        py = '/opt/kwant-venv/bin/python'
        if not os.path.isfile(py):
            return _bad(resp, 'kwant venv absent in this worker',
                        falcon.HTTP_503)
        timeout = min(float(body.get('timeout', 1200) or 1200), 7200.0)
        try:
            run = subprocess.run([py, '/srv/kwant_worker.py'],
                                 input=json.dumps(job),
                                 capture_output=True, text=True,
                                 timeout=timeout)
        except subprocess.TimeoutExpired:
            return _bad(resp, f'kwant worker exceeded {timeout:.0f}s',
                        falcon.HTTP_504)
        if run.returncode != 0:
            resp.status = falcon.HTTP_422
            resp.media = {'ok': False, 'error': 'kwant worker failed',
                          'stderr': run.stderr[-1200:]}
            return
        try:
            resp.media = json.loads(run.stdout)
        except Exception:
            resp.status = falcon.HTTP_422
            resp.media = {'ok': False, 'error': 'worker emitted no JSON',
                          'stdout': run.stdout[-500:],
                          'stderr': run.stderr[-500:]}


app = falcon.App()
app.add_route('/capability', CapabilityResource())
app.add_route('/system-info', SystemInfoResource())
app.add_route('/osdi/compile', OsdiCompileResource())
app.add_route('/ngspice/run', NgspiceRunResource())
app.add_route('/sta/run', StaRunResource())
app.add_route('/kwant/run', KwantRunResource())
