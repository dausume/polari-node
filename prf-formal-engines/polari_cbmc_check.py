#!/usr/bin/env python3
"""polari-cbmc-check — ONE bounded model check of firmware C (sc-2b, FIRMWARE_SCENARIO_PLAN.md §5 tier 2): the three CBMC
steps a property needs, each measured, written to result.json (+ the counterexample trace to trace.json):

  1. goto-cc        compile the harness (which #includes the firmware's own hal.c, unedited) to a goto program, with the
                    integer widths asked for (--16 = LP32: int 16 bits like avr-gcc; --32 = ILP32)
  2. goto-instrument  the interrupt model: --nondet-volatile-model VAR:FN replaces every read of a volatile VAR by FN()
                    (the AVR's byte-wise loads with an interrupt possible between them); --isr FN lets FN run between
                    any two statements that touch a variable FN shares (goto-instrument's interrupt instrumentation)
  3. cbmc           --function ENTRY --unwind K --unwinding-assertions --json-ui --trace: every assertion decided up to
                    the bound, a failing one with its trace

    polari-cbmc-check --src harness.c [--src more.c]... [-I DIR]... [-D N=V]... [--width 16|32] [--volatile-model VAR:FN]... [--isr FN]
                      [--entry main] [--unwind K] [--timeout S] [--out result.json] [--trace trace.json]

verdict: holds (every property SUCCESS, the unwinding assertions too) | refuted (a property FAILED — trace kept) |
bound-too-small (only unwinding assertions failed: nothing is decided at this bound) | refused-at-compile (goto-cc
rejected the source, e.g. a _Static_assert) | timeout | error. The caller maps it onto his vocabulary; this script never
says `proved`. Argv only, no shell. Licence: ours (the worker's); CBMC itself is a separate process (BSD-4-clause style).
"""
import argparse
import json
import os
import subprocess
import sys
import time


def run_measured(argv, timeout):
    """Popen + os.wait4 so the peak RSS is the step's own (RUSAGE_CHILDREN would be the max over every step so far)."""
    import threading
    t0 = time.perf_counter()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks = {'out': [], 'err': []}

    def pump(stream, key):
        for block in iter(lambda: stream.read(65536), b''):
            chunks[key].append(block)
    th = [threading.Thread(target=pump, args=(p.stdout, 'out')), threading.Thread(target=pump, args=(p.stderr, 'err'))]
    for t in th:
        t.start()
    timed_out = False
    deadline = t0 + timeout
    ru = None
    while True:
        pid, status, ru_ = os.wait4(p.pid, os.WNOHANG)
        if pid:
            ru = ru_
            p.returncode = os.waitstatus_to_exitcode(status)
            break
        if time.perf_counter() > deadline:
            p.kill()
            pid, status, ru = os.wait4(p.pid, 0)
            p.returncode = None
            timed_out = True
            break
        time.sleep(0.005)
    for t in th:
        t.join()
    res = {'argv': argv, 'rc': p.returncode, 'timeout': timed_out, 'wall_s': round(time.perf_counter() - t0, 3),
           'stdout': b''.join(chunks['out']).decode('utf-8', 'replace'), 'stderr': b''.join(chunks['err']).decode('utf-8', 'replace')}
    if ru is not None:
        res.update(cpu_s=round(ru.ru_utime + ru.ru_stime, 3), peak_rss_mb=round(ru.ru_maxrss / 1024.0, 1))
    return res


def version():
    try:
        return subprocess.run(['cbmc', '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return 'unknown (%s)' % e


def _messages(stdout):
    try:
        d = json.loads(stdout)
    except ValueError:
        start = stdout.find('[')
        try:
            d = json.loads(stdout[start:]) if start >= 0 else []
        except ValueError:
            d = []
    return d if isinstance(d, list) else [d]


def summarise_trace(trace, limit=200):
    """The failing property's trace → the steps a person reads: assignments (lhs = value, where), function calls/returns."""
    out = []
    for st in trace or []:
        kind = st.get('stepType')
        loc = st.get('sourceLocation') or {}
        where = '%s:%s %s' % (os.path.basename(loc.get('file', '') or ''), loc.get('line', ''), loc.get('function', '') or '')
        if kind == 'assignment' and not st.get('hidden') and st.get('assignmentType') in ('variable', 'state', 'actual-parameter'):
            lhs = st.get('lhs', '')
            if lhs.startswith('__CPROVER') or lhs.startswith('return_value___CPROVER') or 'tmp' in lhs.split('::')[-1]:
                continue
            v = st.get('value') or {}
            out.append({'step': 'assign', 'lhs': lhs, 'value': v.get('data', v.get('name', '')), 'binary': v.get('binary', ''), 'where': where.strip()})
        elif kind in ('function-call', 'function-return') and not st.get('hidden'):
            fn = (st.get('function') or {}).get('displayName') or (st.get('function') or {}).get('identifier', '')
            if fn and not fn.startswith('__CPROVER'):
                out.append({'step': 'call' if kind == 'function-call' else 'return', 'function': fn, 'where': where.strip()})
        elif kind == 'failure':
            out.append({'step': 'FAILURE', 'property': st.get('property', ''), 'reason': st.get('reason', ''), 'where': where.strip()})
        if len(out) >= limit:
            break
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog='polari-cbmc-check')
    ap.add_argument('--src', required=True, action='append')
    ap.add_argument('-I', dest='inc', action='append', default=[])
    ap.add_argument('-D', dest='defs', action='append', default=[])
    ap.add_argument('--width', choices=('16', '32'), default='32')
    ap.add_argument('--volatile-model', action='append', default=[])
    ap.add_argument('--isr', default='')
    ap.add_argument('--entry', default='main')
    ap.add_argument('--unwind', type=int, default=4)
    ap.add_argument('--timeout', type=float, default=300.0)
    ap.add_argument('--out', default='result.json')
    ap.add_argument('--trace', default='trace.json')
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    res = {'tool': 'polari-cbmc-check', 'cbmc_version': version(), 'width': a.width, 'entry': a.entry, 'unwind': a.unwind,
           'volatile_models': a.volatile_model, 'isr': a.isr, 'steps': [], 'properties': [], 'verdict': 'error'}
    budget = a.timeout
    cc = ['goto-cc', '--%s' % a.width] + ['-I%s' % i for i in a.inc] + ['-D%s' % d for d in a.defs] + list(a.src) + ['-o', 'model.gb']
    s = run_measured(cc, budget)
    res['steps'].append(dict(s, step='goto-cc', stdout=s['stdout'][-4000:], stderr=s['stderr'][-4000:]))
    if s['timeout'] or s['rc'] != 0:
        msg = s['stderr'] + s['stdout']
        res['verdict'] = 'timeout' if s['timeout'] else ('refused-at-compile' if 'static assertion' in msg.lower() or 'static_assert' in msg.lower() else 'error')
        res['why'] = msg.strip().splitlines()[-6:] if msg.strip() else ['goto-cc rc %s' % s['rc']]
        return _write(a, res, t0, [])
    gi = ['goto-instrument']
    for m in a.volatile_model:
        gi += ['--nondet-volatile-model', m]
    if a.isr:
        gi += ['--isr', a.isr]
    gi += ['model.gb', 'model-isr.gb']
    model = 'model.gb'
    if len(gi) > 3:
        s = run_measured(gi, max(1.0, budget - (time.perf_counter() - t0)))
        res['steps'].append(dict(s, step='goto-instrument', stdout=s['stdout'][-4000:], stderr=s['stderr'][-4000:]))
        if s['timeout'] or s['rc'] != 0:
            res['verdict'] = 'timeout' if s['timeout'] else 'error'
            res['why'] = (s['stderr'] + s['stdout']).strip().splitlines()[-6:]
            return _write(a, res, t0, [])
        model = 'model-isr.gb'
    cb = ['cbmc', model, '--function', a.entry, '--unwind', str(a.unwind), '--unwinding-assertions', '--json-ui', '--trace']
    s = run_measured(cb, max(1.0, budget - (time.perf_counter() - t0)))
    res['steps'].append(dict(s, step='cbmc', stdout=s['stdout'][-2000:], stderr=s['stderr'][-4000:]))
    if s['timeout']:
        res['verdict'] = 'timeout'
        return _write(a, res, t0, [])
    msgs = _messages(s['stdout'])
    results, traces = [], []
    for m in msgs:
        if isinstance(m, dict) and 'result' in m:
            results = m['result']
        if isinstance(m, dict) and m.get('messageText', '').startswith('Runtime'):
            res.setdefault('runtime', []).append(m['messageText'])
        if isinstance(m, dict) and 'messageText' in m and ('size of program expression' in m['messageText'] or 'variables' in m['messageText']):
            res.setdefault('formula', []).append(m['messageText'])
    for r in results:
        p = {'property': r.get('property', ''), 'status': r.get('status', ''), 'description': r.get('description', ''),
             'where': '%s:%s' % (os.path.basename((r.get('sourceLocation') or {}).get('file', '') or ''), (r.get('sourceLocation') or {}).get('line', ''))}
        res['properties'].append(p)
        if r.get('status') == 'FAILURE':
            traces.append({'property': p['property'], 'description': p['description'], 'steps': summarise_trace(r.get('trace')), 'raw_steps': len(r.get('trace') or [])})
    failed = [p for p in res['properties'] if p['status'] == 'FAILURE']
    unwind_only = failed and all('.unwind.' in p['property'] or 'unwinding assertion' in p['description'] for p in failed)
    if s['rc'] == 0 and res['properties'] and not failed:
        res['verdict'] = 'holds'
    elif failed and unwind_only:
        res['verdict'] = 'bound-too-small'
    elif failed:
        res['verdict'] = 'refuted'
    else:
        res['verdict'] = 'error'
        res['why'] = (s['stderr'] or s['stdout']).strip().splitlines()[-6:]
    return _write(a, res, t0, traces)


def _write(a, res, t0, traces):
    res['wall_s'] = round(time.perf_counter() - t0, 3)
    res['peak_rss_mb'] = max([st.get('peak_rss_mb', 0.0) for st in res['steps']] or [0.0])
    res['cpu_s'] = round(sum(st.get('cpu_s', 0.0) for st in res['steps']), 3)
    json.dump(traces, open(a.trace, 'w'), indent=1)
    json.dump(res, open(a.out, 'w'), indent=1)
    print(json.dumps({'verdict': res['verdict'], 'properties': len(res['properties']), 'failed': sum(p['status'] == 'FAILURE' for p in res['properties']),
                      'wall_s': res['wall_s'], 'peak_rss_mb': res['peak_rss_mb'], 'cbmc': res['cbmc_version']}))
    return 0 if res['verdict'] in ('holds', 'refuted', 'bound-too-small', 'refused-at-compile') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
