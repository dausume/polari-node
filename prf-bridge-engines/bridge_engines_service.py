"""
bridge-engines - the HOSTED BRIDGE worker (sim-2, AI-Notes/plans/HARDWARE_BENCH_CONVERGENCE_PLAN.md): the generated Polari Hardware Bridge, built and run
as a process beside a twin session, so the web page alone is a complete test (no desktop app). The Polari engines pattern (board-engines / pcb-engines):
the framework resolves it through polari-framework/modules/grpcbridge/custom/bridge_engines.py - BRIDGE_ENGINES_URL knob -> the topology provider
`grpcbridge.engines` -> a refusal in words.

  GET  /capability   {worker, engines: {java, mvn: {available, version}}, bridges: {available, max, active, ...}, resources (res-2 block)}
  GET  /system-info  res-1 shape + the res-3 process block
  GET/POST /bridges, GET/DELETE /bridges/{id}, GET /bridges/{id}/logs      bridge_hosted.py
"""
import json
import os
import shutil
import subprocess

import falcon

import bridge_hosted


def _version(binary, args):
    b = shutil.which(binary)
    if not b:
        return ''
    try:
        r = subprocess.run([b] + args, capture_output=True, text=True, timeout=30)
        out = (r.stdout or r.stderr or '').strip().splitlines()
        return out[0][:160] if out else 'present'
    except Exception:
        return 'present'


def _resources_block():
    try:
        measured = json.load(open('/srv/cost.json'))
    except Exception:
        measured = {}
    return dict({'ramMb': 160, 'minThreads': 1, 'threadCeiling': 2, 'cpuBenefit': 'none', 'fidelity': 'declared',
                 'note': 'res-2 block (sim-2): one JVM (-Xmx160m) per hosted bridge; a jar build is one mvn run; measured values in modules/grpcbridge/COST.md'},
                **measured)


class Capability:
    def on_get(self, req, resp):
        resp.media = {'worker': 'bridge-engines', 'resources': _resources_block(),
                      'engines': {'java': {'available': bool(shutil.which('java')), 'version': _version('java', ['-version']), 'binary': 'java'},
                                  'mvn': {'available': bool(shutil.which('mvn')), 'version': _version('mvn', ['-B', '-v']), 'binary': 'mvn'}},
                      'bridges': bridge_hosted.info(),
                      'protocol': 'POST /bridges {session_id, twin_host, twin_port, bridge_name, files, grpc_target?} - builds the generated project (cached by sha) and '
                                  'runs it with source=twin-tcp; GET/DELETE /bridges/{id}; GET /bridges/{id}/logs'}


class SystemInfo:
    def on_get(self, req, resp):
        import platform
        info, proc = {}, {}
        try:
            for line in open('/proc/meminfo'):
                k, v = line.split(':', 1)
                if k in ('MemTotal', 'MemAvailable'):
                    info[k] = int(v.split()[0]) * 1024
            for line in open('/proc/self/status'):
                if line.startswith('VmRSS:'):
                    proc['residentMb'] = round(int(line.split()[1]) / 1024.0, 1)
        except Exception:
            pass
        resp.media = {'ok': True, 'worker': 'bridge-engines', 'process': proc, 'cpus': os.cpu_count(), 'memTotalBytes': info.get('MemTotal', 0),
                      'memAvailableBytes': info.get('MemAvailable', 0), 'platform': platform.platform()}


app = falcon.App(middleware=[bridge_hosted.Reaper()])
app.add_route('/capability', Capability())
app.add_route('/system-info', SystemInfo())
bridge_hosted.add_routes(app)
