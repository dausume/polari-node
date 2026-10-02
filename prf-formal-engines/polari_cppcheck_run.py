#!/usr/bin/env python3
"""polari-cppcheck-run — cppcheck over one firmware project → findings.json (sc-2b, FIRMWARE_SCENARIO_PLAN.md §4 "static
rules for 8-bit"; D-sc-6 named cppcheck). Findings are ROWS for the caller, never a build failure: this exits 0 whenever
cppcheck itself ran, whatever it found.

    polari-cppcheck-run [--platform avr8] [--std c99] [--enable warning,style,portability,performance] [--addon threadsafety]
                        [-I DIR]... [-D N=V]... [--out findings.json] FILE.c...

What is run, said plainly: cppcheck's BUILT-IN checks (the --enable classes) + the `threadsafety` addon. The MISRA addon is
NOT run — its rule texts need the non-free MISRA C document (--rule-texts), and rule numbers without texts would be noise.
`missingIncludeSystem` / `missingInclude` / `checkersReport` are information about the run, kept apart (severity
information), not findings. Measured: wall, CPU-s, peak RSS (wait4).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

RUN_INFO = ('missingIncludeSystem', 'missingInclude', 'checkersReport', 'normalCheckLevelMaxBranches', 'toomanyconfigs', 'unmatchedSuppression')


def main(argv):
    ap = argparse.ArgumentParser(prog='polari-cppcheck-run')
    ap.add_argument('--platform', default='avr8')
    ap.add_argument('--std', default='c99')
    ap.add_argument('--enable', default='warning,style,portability,performance')
    ap.add_argument('--addon', action='append', default=[])
    ap.add_argument('-I', dest='inc', action='append', default=[])
    ap.add_argument('-D', dest='defs', action='append', default=[])
    ap.add_argument('--out', default='findings.json')
    ap.add_argument('--timeout', type=float, default=300.0)
    ap.add_argument('files', nargs='+')
    a = ap.parse_args(argv)
    ver = subprocess.run(['cppcheck', '--version'], capture_output=True, text=True, timeout=30).stdout.strip()
    cmd = ['cppcheck', '--enable=%s' % a.enable, '--platform=%s' % a.platform, '--std=%s' % a.std, '--xml', '--xml-version=2',
           '--quiet', '--inline-suppr', '--force'] + ['--addon=%s' % x for x in a.addon] + ['-I%s' % i for i in a.inc] + \
          ['-D%s' % d for d in a.defs] + list(a.files)
    t0 = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = p.communicate(timeout=a.timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    wall = round(time.perf_counter() - t0, 3)
    try:
        import resource
        ru = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu, rss = round(ru.ru_utime + ru.ru_stime, 3), round(ru.ru_maxrss / 1024.0, 1)
    except Exception:  # noqa: BLE001
        cpu, rss = 0.0, 0.0
    text = err.decode('utf-8', 'replace')
    findings, info = [], []
    try:
        root = ET.fromstring(text[text.find('<?xml'):] if '<?xml' in text else text)
        for e in root.iter('error'):
            locs = e.findall('location')
            loc = locs[0].attrib if locs else {}
            f = {'id': e.get('id', ''), 'severity': e.get('severity', ''), 'message': e.get('msg', ''), 'verbose': e.get('verbose', ''),
                 'cwe': int(e.get('cwe', '0') or 0), 'file': os.path.basename(loc.get('file', '')), 'line': int(loc.get('line', '0') or 0),
                 'column': int(loc.get('column', '0') or 0), 'symbol': (e.findtext('symbol') or ''),
                 'addon': e.get('id', '').split('-')[0] if '-' in e.get('id', '') and e.get('id', '').split('-')[0] in ('threadsafety', 'misra', 'y2038') else ''}
            (info if f['id'] in RUN_INFO or f['severity'] == 'information' else findings).append(f)
        parsed = True
    except ET.ParseError as ex:
        parsed = False
        info.append({'id': 'xml-parse', 'severity': 'information', 'message': str(ex), 'file': '', 'line': 0})
    counts = {}
    for f in findings:
        counts[f['severity']] = counts.get(f['severity'], 0) + 1
    res = {'tool': 'polari-cppcheck-run', 'cppcheck_version': ver, 'argv': cmd, 'rc': p.returncode, 'parsed': parsed, 'wall_s': wall, 'cpu_s': cpu,
           'peak_rss_mb': rss, 'findings': findings, 'run_info': info, 'counts': counts, 'files': [os.path.basename(f) for f in a.files],
           'not_run': 'misra (rule texts need the non-free MISRA C document)'}
    json.dump(res, open(a.out, 'w'), indent=1)
    print(json.dumps({'findings': len(findings), 'counts': counts, 'run_info': len(info), 'wall_s': wall, 'cppcheck': ver}))
    return 0 if parsed else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
