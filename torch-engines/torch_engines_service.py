"""
torch-engines — the PyTorch engine WORKER (the Polari engines pattern: eda-engines / proof-engines / cnt-engines): a
pinned CPU PyTorch behind one JSON API (:9820), so tensormath's `stress-from-strain/torch` ComputeImplementation runs on
whatever device the topology assigns (`pol allocate tensormath.engines <instance>`) while the core keeps the rows. The
backend resolves through polari-framework/modules/tensormath/custom/torch_engine.py — TORCH_ENGINES_URL knob wins; else
`import torch` in the framework process; else the topology's provider; else an honest refusal.

  GET  /capability   {worker, engines: {torch: {available, version, device, threads, deterministic}}}
  GET  /system-info  res-1 shape (cpu/mem) like the other workers
  POST /evaluate     {"einsum": "ijkl,nkl->ijn", "operands": [nested lists...], "dtype": "float64"}
                     -> {ok, values, elapsed_s (the einsum alone), torch: {version, device, threads, dtype}}
                     einsum ONLY — no arbitrary code; operand size is capped (MAX_ELEMENTS) so a request cannot exhaust the worker.
"""
import json
import os
import time

import falcon

MAX_ELEMENTS = int(os.environ.get('TORCH_MAX_ELEMENTS', '5000000'))


def _torch():
    import torch
    try:
        torch.use_deterministic_algorithms(True)
    except Exception:
        pass
    return torch


def _count(x):
    if isinstance(x, list):
        return sum(_count(v) for v in x) if x and isinstance(x[0], list) else len(x)
    return 1


class Capability:
    def on_get(self, req, resp):
        try:
            t = _torch()
            resp.media = {'worker': 'torch-engines', 'engines': {'torch': {'available': True, 'version': str(t.__version__), 'device': 'cpu', 'threads': int(t.get_num_threads()),
                                                                          'deterministic': True, 'note': 'einsum only; CPU wheel; use_deterministic_algorithms(True)'}}}
        except Exception as exc:
            resp.media = {'worker': 'torch-engines', 'engines': {'torch': {'available': False, 'error': str(exc)}}}


class SystemInfo:
    def on_get(self, req, resp):
        try:
            import psutil
            vm = psutil.virtual_memory()
            resp.media = {'ok': True, 'cpu_count': os.cpu_count(), 'cpu_percent': psutil.cpu_percent(interval=0.1), 'mem_total_bytes': vm.total, 'mem_available_bytes': vm.available, 'worker': 'torch-engines'}
        except Exception:
            resp.media = {'ok': True, 'cpu_count': os.cpu_count(), 'worker': 'torch-engines'}


class Evaluate:
    def on_post(self, req, resp):
        body = req.media if isinstance(req.media, dict) else {}
        spec = str(body.get('einsum', '') or ''); ops = body.get('operands') or []; dtype = str(body.get('dtype', 'float64') or 'float64')
        if not spec or '->' not in spec or not isinstance(ops, list) or not ops:
            resp.status = falcon.HTTP_422; resp.media = {'ok': False, 'error': 'einsum (with ->) and a non-empty operands list are required'}; return
        if dtype not in ('float64', 'float32'):
            resp.status = falcon.HTTP_422; resp.media = {'ok': False, 'error': 'dtype must be float64 or float32'}; return
        n = sum(_count(o) for o in ops)
        if n > MAX_ELEMENTS:
            resp.status = falcon.HTTP_413; resp.media = {'ok': False, 'error': 'operands hold %d elements > TORCH_MAX_ELEMENTS %d' % (n, MAX_ELEMENTS)}; return
        try:
            t = _torch()
            dt = getattr(t, dtype)
            ts = [t.tensor(o, dtype=dt) for o in ops]
            t0 = time.perf_counter()
            r = t.einsum(spec, *ts)
            el = time.perf_counter() - t0
            resp.media = {'ok': True, 'values': r.tolist(), 'shape': list(r.shape), 'elapsed_s': round(el, 6),
                          'torch': {'version': str(t.__version__), 'device': str(r.device), 'threads': int(t.get_num_threads()), 'dtype': dtype, 'deterministic': True}}
        except Exception as exc:
            resp.status = falcon.HTTP_422; resp.media = {'ok': False, 'error': 'torch.einsum failed: %s' % exc}


app = falcon.App()
app.add_route('/capability', Capability())
app.add_route('/system-info', SystemInfo())
app.add_route('/evaluate', Evaluate())
