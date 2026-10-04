#!/usr/bin/env python3
"""polari-vcd-window — the engine-side reader of polari-avr-twin's VCD (sc-0, FIRMWARE_SCENARIO_PLAN.md §6, D-sc-6):
pyvcd (MIT, github.com/westerndigitalcorporation/pyvcd, Debian python3-pyvcd) tokenizes the trace; this prints the
instruction samples around one cycle as JSON — the rows of the "cycles around the fault" table. No interpretation
here (symbols, the verdict): the framework adds those from the ELF.

    polari-vcd-window trace.vcd [--center CYCLE] [--before 12] [--after 24]

Output: one JSON object {"ok", "ps_per_cycle", "signals", "samples": [{"cycle", "pc", "sp", "sreg_i", "isr_vector",
"r22".."r25", "<watch>", "forced"}, ...], "sha256"}.
"""
import hashlib
import io
import json
import re
import sys

from vcd.reader import TokenKind, tokenize


def read(path):
    raw = open(path, 'rb').read()
    ps = 62500
    m = re.search(rb'cycle x (\d+) ps', raw)
    if m:
        ps = int(m.group(1))
    ids, cur, samples, t = {}, {}, [], None
    for tok in tokenize(io.BytesIO(raw)):
        k = tok.kind
        if k is TokenKind.VAR:
            ids[tok.data.id_code] = tok.data.reference
        elif k is TokenKind.CHANGE_TIME:
            if t is not None:
                samples.append(dict(cur, cycle=t // ps))
            t = tok.data
        elif k in (TokenKind.CHANGE_VECTOR, TokenKind.CHANGE_SCALAR):
            v = tok.data.value
            cur[ids[tok.data.id_code]] = int(v) if not isinstance(v, str) else (int(v) if v.isdigit() else v)
    if t is not None:
        samples.append(dict(cur, cycle=t // ps))
    return raw, ps, sorted(set(ids.values())), samples


def main(argv):
    if argv and argv[0] == '--version':
        try:
            from importlib.metadata import version
            v = version('pyvcd')
        except Exception:
            v = 'unknown'
        print('polari-vcd-window (pyvcd %s)' % v)
        return 0
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0 if argv else 2
    path = argv[0]
    opt = {k: int(argv[argv.index('--' + k) + 1]) for k in ('center', 'before', 'after') if '--' + k in argv}
    raw, ps, sigs, samples = read(path)
    if 'center' in opt:
        idx = next((i for i, s in enumerate(samples) if s['cycle'] >= opt['center']), len(samples) - 1)
        samples = samples[max(0, idx - opt.get('before', 12)): idx + opt.get('after', 24) + 1]
    print(json.dumps({'ok': True, 'ps_per_cycle': ps, 'signals': sigs, 'samples': samples, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'reader': 'pyvcd (vcd.reader.tokenize)'}))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
