#!/usr/bin/env python3
"""polari-mthread-check — ONE Frama-C/Mthread race analysis of firmware C (sc-2c, FIRMWARE_SCENARIO_PLAN.md §5 tier 2, D-sc-6
ruled 2026-10-02): `frama-c -eva -mthread` over a harness that #includes the firmware's own hal.c, measured with wait4, its
report parsed into result.json:

    polari-mthread-check --src harness.c [-I DIR]... [-D N=V]... [--machdep avr_16] [--entry main] [--slevel 15] [--timeout S] [--out result.json]

--slevel: Eva keeps that many states apart per statement. ATOMIC_BLOCK is a for-loop (lock on entry, unlock on exit): with
slevel 0 Eva merges the entry state (locked) with the exit state (unlocked) at the loop head and Mthread reports the read
as protected by "(?)" — maybe; slevel >= 2 keeps them apart (measured 2026-10-02). 15 = Frama-C's own Mthread test setting.

result.json:
    verdict      analysed (Mthread ran to its fixed point; `shared` lists every variable it saw accessed by two threads) |
                 refused-at-compile (the source does not compile — e.g. hal.c's _Static_assert) | timeout | error
    shared       [{var, accesses: [{kind read|write, thread, where file:line, protected_by: [mutex…], maybe: bool}]}] — Mthread's
                 "Possible read/write data races" section verbatim as data (a `(?)` mutex = protected on SOME paths only → maybe)
    sizes        {var: bytes} from the harness's Frama_C_show_each_polari_size_<var>(sizeof …) calls (Eva prints the value)
    threads      the thread names Mthread reported; mt_lines: the LAST race report (Mthread prints one per iteration; the last is
                 the fixed point) + its iteration count, verbatim
    frama_c_version, argv, wall_s, cpu_s, peak_rss_mb (wait4: frama-c's own)

This script decides NOTHING about safety: the classification (protected / byte-atomic single writer / race) is the
framework's (firmwarefaults.custom.formal_mthread.classify), so the rule is testable without the engine. Argv only, no
shell. Licence: ours (the worker's); Frama-C (LGPL-2.1-only) is a separate process.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ACCESS = re.compile(r'^\s*(read|write)\s+by\s+(\S+)\s+at\s+(\S+?):(\d+),\s*(.*)$')
VAR = re.compile(r'^\s{1,4}([A-Za-z_][\w\[\].>-]*)\s*:\s*$')
SIZE = re.compile(r'Frama_C_show_each_polari_size_([A-Za-z_]\w*)\s*:\s*\{?\s*(\d+)\s*\}?')


def run_measured(argv, timeout):
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
    timed_out, ru = False, None
    deadline = t0 + timeout
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
        return 'frama-c ' + subprocess.run(['frama-c', '-version'], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return 'unknown (%s)' % e


def parse_protection(tail):
    """'unprotected' | 'protected by m1 (?)m2' (Mthread separates by spaces; commas tolerated) → (mutexes, maybe)."""
    t = tail.strip().rstrip('.')
    if t.startswith('unprotected') or not t.startswith('protected'):
        return [], False
    names = [n.strip() for n in re.split(r'[\s,]+', t[len('protected by'):]) if n.strip()]
    maybe = any(n.startswith('(?)') for n in names)
    return [n.replace('(?)', '') for n in names], maybe


def parse(out):
    """Mthread's LAST race report (one per iteration; the last = the fixed point) → {shared, threads, mt_lines, iterations};
    Eva's show lines → sizes."""
    lines = out.splitlines()
    shared, cur, in_races = [], None, False
    mt_lines, iterations = [], None
    for ln in lines:
        if ln.startswith('[mt]'):
            m = re.search(r'Analysis performed, (\d+) iterations', ln)
            if m:
                iterations = int(m.group(1))
            in_races = 'data races' in ln.lower()
            if in_races:
                shared, mt_lines = [], [ln]
            cur = None
            continue
        if ln.startswith('['):           # another plugin's message ends the section
            in_races = False
            cur = None
            continue
        if not in_races:
            continue
        mt_lines.append(ln)
        m = ACCESS.match(ln)
        if m and cur is not None:
            prot, maybe = parse_protection(m.group(5))
            cur['accesses'].append({'kind': m.group(1), 'thread': m.group(2), 'where': '%s:%s' % (os.path.basename(m.group(3)), m.group(4)),
                                    'protected_by': prot, 'maybe': maybe, 'text': ln.strip()})
            continue
        v = VAR.match(ln)
        if v:
            cur = {'var': v.group(1), 'accesses': []}
            shared.append(cur)
    sizes = {}
    for m in SIZE.finditer(out):
        sizes[m.group(1)] = int(m.group(2))
    threads = sorted({a['thread'] for s in shared for a in s['accesses']})
    return {'shared': shared, 'sizes': sizes, 'threads': threads, 'mt_lines': mt_lines, 'iterations': iterations}



def main(argv):
    ap = argparse.ArgumentParser(prog='polari-mthread-check')
    ap.add_argument('--src', required=True, action='append')
    ap.add_argument('-I', dest='inc', action='append', default=[])
    ap.add_argument('-D', dest='defs', action='append', default=[])
    ap.add_argument('--machdep', default='avr_16')
    ap.add_argument('--entry', default='main')
    ap.add_argument('--slevel', type=int, default=15)
    ap.add_argument('--timeout', type=float, default=300.0)
    ap.add_argument('--out', default='result.json')
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    cpp = ' '.join(['-I%s' % i for i in a.inc] + ['-D%s' % d for d in a.defs])
    # Debian's cpp (not gcc: the image carries the preprocessor only); Frama-C puts its own libc (share/libc) and, for
    # -mthread, share/mt (mthread.h) on the include path BEFORE these -I's — <stdint.h> is Frama-C's, sized by the machdep
    fc = ['frama-c', '-machdep', a.machdep, '-cpp-command', 'cpp -C', '-cpp-frama-c-compliant', '-cpp-extra-args=%s' % cpp, '-main', a.entry,
          '-eva', '-eva-slevel', str(a.slevel), '-mthread', '-mt-threads-lib', 'builtins-only', '-mt-verbose', '1'] + list(a.src)
    res = {'tool': 'polari-mthread-check', 'frama_c_version': version(), 'machdep': a.machdep, 'entry': a.entry, 'slevel': a.slevel, 'verdict': 'error',
           'shared': [], 'sizes': {}, 'threads': [], 'mt_lines': []}
    s = run_measured(fc, a.timeout)
    res['step'] = {k: s.get(k) for k in ('argv', 'rc', 'timeout', 'wall_s', 'cpu_s', 'peak_rss_mb')}
    out = s['stdout'] + '\n' + s['stderr']
    res['output_tail'] = out[-6000:]
    if s['timeout']:
        res['verdict'] = 'timeout'
    elif s['rc'] != 0:
        low = out.lower()
        res['verdict'] = 'refused-at-compile' if ('static assert' in low or 'static_assert' in low) else 'error'
        res['why'] = [ln for ln in out.strip().splitlines() if 'error' in ln.lower() or 'assert' in ln.lower()][-6:] or out.strip().splitlines()[-6:]
    else:
        res.update(parse(out))
        res['verdict'] = 'analysed' if res['mt_lines'] and res['iterations'] else 'error'
        if res['verdict'] == 'error':
            res['why'] = ['frama-c ran but Mthread printed no race report after an iteration (%s iterations) — the threads were never '
                          'analysed (a thread created but not started?)' % res['iterations']]
    res['wall_s'] = round(time.perf_counter() - t0, 3)
    res['cpu_s'] = s.get('cpu_s', 0.0)
    res['peak_rss_mb'] = s.get('peak_rss_mb', 0.0)
    json.dump(res, open(a.out, 'w'), indent=1)
    print(json.dumps({'verdict': res['verdict'], 'shared': [x['var'] for x in res['shared']], 'sizes': res['sizes'], 'wall_s': res['wall_s'],
                      'peak_rss_mb': res['peak_rss_mb'], 'frama_c': res['frama_c_version']}))
    return 0 if res['verdict'] in ('analysed', 'refused-at-compile') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
