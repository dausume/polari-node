#!/usr/bin/env python3
"""
polari-c3-run — boot a merged C3 flash image in Espressif's QEMU fork (`qemu-system-riscv32 -machine esp32c3`) and
collect what the firmware said (sc-3; the twin of FIRMWARE_SCENARIO_PLAN.md §9 sc-3).

  SCENARIO mode (a file-in / files-out engine through /run or `docker run`):
    polari-c3-run flash_image.bin [--params HEX] [--params-offset 0x110000] [--icount 3] [--max-wall 180] [--until @END]
      --params      the bytes to write at --params-offset in a COPY of the image (the `polari` data partition: the
                    scenario's knobs + seed, polari_c3.h polari_params_t) — the same binary, steered per seed
      --icount N    QEMU's instruction counting, shift N (2^N ns of virtual time per instruction), align=off, sleep=off:
                    virtual time is a function of the instruction stream alone (idle time warps to the next timer), so
                    a run is a function of (image, params) — Espressif's docs: the C3 machine needs -icount
      --until TEXT  stop when a line starting with TEXT appears on UART1 (the firmware's end-of-window line)
    out  uart0.bin   every byte of UART0 (the SimRigState frames, after the ROM's boot text)
         trace.log   every line of UART1 (the trace channel: @BOOT, @EV …, the decision lines, @END)
         run.json    {ended, why, wall_s, cpu_s, peak_rss_mb (wait4 on QEMU), argv, qemu_version, uart0_bytes, trace_lines}

    smp S5 (host-driven runs, the C3 light Mission):
      --rx-plan FILE   a JSON list [{"file": "on.bin", "after": "@TEL", "count": 5}, ..., {"file": "", "after": "@TEL", "count": 5, "end": true}] (file = a
                       basename in the job dir, '' = send nothing): UART0 becomes a TCP socket this engine owns (QEMU waits for it before it starts), and a
                       step's file is written to UART0 RX once `count` trace (UART1) lines that START with `after` have appeared since the previous step.
                       The firmware's own `@TEL n` lines (one per telemetry frame) pace the host in the firmware's VIRTUAL time: idle time warps in
                       -icount, so a wall-clock pace would race. `end` stops the run (then --until is not needed: pass --until NONE).
      --boots N        run N consecutive boots on the SAME flash copy (a power cycle each time, flash kept - NVS counters survive); uart0.bin and trace.log
                       are the boots' outputs in order, trace.log with a `@POWER-CYCLE n` line between them.

  TWIN mode (a long-lived container, `pol board twin c3 up`):
    polari-c3-run flash_image.bin --serve PORT [--trace FILE]
      UART0 on TCP :PORT (server, nowait — the host's pty pump dials it), UART1 to FILE, -icount shift=auto,sleep=on so
      virtual time tracks the wall clock (the bridge sees ~real time); runs until stopped.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

QEMU = 'qemu-system-riscv32'


def qemu_version():
    try:
        return subprocess.run([QEMU, '--version'], capture_output=True, text=True, timeout=20).stdout.splitlines()[0].strip()
    except Exception:
        return ''


def patch(src, dst, params_hex, offset):
    shutil.copy(src, dst)
    if params_hex:
        data = bytes.fromhex(params_hex)
        with open(dst, 'r+b') as f:
            f.seek(offset)
            f.write(data)


def base_argv(image, icount, realtime):
    """scenario: -icount shift=N,align=off,sleep=off — virtual time is the instruction count alone (2^N ns each; N = 3 is
    Espressif's documented value, 125 M instructions per virtual second — near the C3's 160 MHz), reproducible, as fast as the
    host runs. twin: -icount shift=auto,sleep=on — QEMU adapts the shift so virtual time tracks the wall clock (measured
    with shift=3 even with align=on,sleep=on: virtual time ran ~5.6x ahead, 100 ms telemetry every ~18 ms)."""
    ic = 'shift=auto,sleep=on' if realtime else 'shift=%d,align=off,sleep=off' % icount
    return [QEMU, '-machine', 'esp32c3', '-display', 'none', '-monitor', 'none', '-nodefaults', '-icount', ic,
            '-drive', 'file=%s,if=mtd,format=raw' % image]


def _pace(p, a, plan, sock, t0, tr, u0):
    """Poll until the end line / the plan's `end` step / exit / max-wall; feed `plan` into UART0 RX; copy UART0's output.
    A plan step fires when `count` lines that start with its `after` text have appeared in the trace SINCE THE PREVIOUS STEP (since the start for the first), counted in
    the firmware's own virtual time (its `@TEL n` lines), so a guest that runs ahead of the wall clock (idle time warps) cannot reorder or skip a step."""
    ended, why, step, base = False, 'max-wall', 0, 0
    while time.perf_counter() - t0 < a['max_wall']:
        if sock:
            try:
                d = sock.recv(65536)
                if d:
                    u0.write(d)
                    u0.flush()
            except OSError:
                pass
        if p.poll() is not None:
            why = 'qemu exited (rc %s)' % p.returncode
            break
        try:
            with open(tr, 'rb') as f:
                lines = f.read().decode('utf-8', 'replace').split('\n')
        except FileNotFoundError:
            lines = []
        if step < len(plan):
            e = plan[step]
            if sum(1 for ln in lines[base:] if ln.startswith(e['after'])) >= int(e.get('count', 1)):
                if e.get('bytes'):
                    sock.sendall(e['bytes'])
                if e.get('end'):
                    ended, why = True, 'plan step %d (end) after %s x%d' % (step, e['after'], int(e.get('count', 1)))
                    break
                step += 1
                base = len(lines) - 1
        if a['until'] and any(ln.startswith(a['until']) for ln in lines):
            ended, why = True, 'saw %s' % a['until']
            break
        if not sock:
            time.sleep(0.05)
    if sock:
        end = time.perf_counter() + 0.3
        while time.perf_counter() < end:      # the last frames still in the socket
            try:
                d = sock.recv(65536)
                if d:
                    u0.write(d)
            except socket.timeout:
                continue
            except OSError:
                break
        u0.flush()
    return ended, why, step


def one_boot(a, img, n):
    """One QEMU boot of `img` (a flash copy kept across boots). -> (ended, why, cost dict, argv, uart0 bytes, trace text, qemu output tail, rx sent)."""
    uart_f, trace_f = 'uart0.%d.bin' % n, 'trace.%d.log' % n
    for f in (uart_f, trace_f):
        if os.path.exists(f):
            os.remove(f)
    plan = a['plan']
    argv = base_argv(img, a['icount'], False)
    if plan:
        port = _free_port()
        argv += ['-serial', 'tcp:127.0.0.1:%d,server=on,wait=on' % port, '-serial', 'file:%s' % trace_f]
    else:
        argv += ['-serial', 'file:%s' % uart_f, '-serial', 'file:%s' % trace_f]
    t0 = time.perf_counter()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sock = None
    u0 = open(uart_f, 'wb') if plan else None
    if plan:
        for _ in range(100):
            try:
                sock = socket.create_connection(('127.0.0.1', port), timeout=0.05)
                break
            except OSError:
                time.sleep(0.05)
        if sock is None:
            p.kill()
            raise SystemExit('polari-c3-run: could not connect to QEMU UART0 on port %d' % port)
        sock.settimeout(0.02)
    ended, why, sent = _pace(p, a, plan, sock, t0, trace_f, u0)
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
    _, status, ru = os.wait4(p.pid, 0)
    wall = time.perf_counter() - t0
    if u0:
        u0.close()
        sock.close()
    out = p.stdout.read().decode('utf-8', 'replace') if p.stdout else ''
    cost = {'wall_s': round(wall, 3), 'cpu_s': round(ru.ru_utime + ru.ru_stime, 3), 'peak_rss_mb': round(ru.ru_maxrss / 1024.0, 1)}
    return ended, why, cost, argv, trace_f, uart_f, out, sent


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def scenario(a):
    patch(a['image'], 'run.bin', a['params'], a['offset'])
    for f in ('uart0.bin', 'trace.log'):
        if os.path.exists(f):
            os.remove(f)
    a['plan'] = []
    if a['rx_plan']:
        a['plan'] = json.load(open(a['rx_plan']))
        for e in a['plan']:
            e['bytes'] = open(os.path.basename(e['file']), 'rb').read() if e.get('file') else b''
    boots, costs = [], []
    for n in range(a['boots']):
        ended, why, cost, argv, trace_f, uart_f, out, sent = one_boot(a, 'run.bin', n)
        boots.append((trace_f, uart_f))
        costs.append(dict(cost, ended=ended, why=why, rx_sent=sent))
        if not ended and a['boots'] == 1:
            break
    os.remove('run.bin')
    with open('uart0.bin', 'wb') as u, open('trace.log', 'wb') as t:
        for i, (tf, uf) in enumerate(boots):
            if i:
                t.write(('@POWER-CYCLE %d\n' % i).encode())
            if os.path.exists(uf):
                u.write(open(uf, 'rb').read())
            if os.path.exists(tf):
                t.write(open(tf, 'rb').read())
            for f in (tf, uf):
                if os.path.exists(f):
                    os.remove(f)
    lines = open('trace.log', 'rb').read().decode('utf-8', 'replace').count('\n')
    ended = all(c['ended'] for c in costs)
    res = {'ended': ended, 'why': '; '.join(c['why'] for c in costs), 'wall_s': round(sum(c['wall_s'] for c in costs), 3),
           'cpu_s': round(sum(c['cpu_s'] for c in costs), 3), 'peak_rss_mb': max(c['peak_rss_mb'] for c in costs), 'argv': argv, 'qemu_version': qemu_version(),
           'icount_shift': a['icount'], 'uart0_bytes': os.path.getsize('uart0.bin'), 'trace_lines': lines, 'boots': costs,
           'params_offset': a['offset'], 'params_bytes': len(a['params']) // 2, 'qemu_output_tail': out[-1500:]}
    json.dump(res, open('run.json', 'w'), indent=1)
    print(json.dumps({k: res[k] for k in ('ended', 'why', 'wall_s', 'peak_rss_mb', 'uart0_bytes', 'trace_lines')}))
    return 0 if ended else 1


def serve(a):
    img = '/tmp/twin_flash.bin'
    patch(a['image'], img, a['params'], a['offset'])
    argv = base_argv(img, a['icount'], True) + ['-serial', 'tcp::%d,server,nowait' % a['serve'], '-serial', 'file:%s' % a['trace']]
    print(json.dumps({'t': 'ready', 'argv': argv, 'qemu_version': qemu_version(), 'tcp': a['serve'], 'trace': a['trace']}), flush=True)
    os.execvp(QEMU, argv)


def main(argv):
    if not argv or argv[0].startswith('-'):
        print(__doc__)
        return 2
    a = {'image': argv[0], 'params': '', 'offset': 0x110000, 'icount': 3, 'max_wall': 180.0, 'until': '@END', 'serve': 0,
         'trace': '/tmp/trace.log', 'rx_plan': '', 'boots': 1}
    i = 1
    while i < len(argv):
        k, v = argv[i], argv[i + 1] if i + 1 < len(argv) else ''
        if k == '--params':
            a['params'] = v
        elif k == '--params-offset':
            a['offset'] = int(v, 0)
        elif k == '--icount':
            a['icount'] = int(v)
        elif k == '--max-wall':
            a['max_wall'] = float(v)
        elif k == '--until':
            a['until'] = v
        elif k == '--serve':
            a['serve'] = int(v)
        elif k == '--trace':
            a['trace'] = v
        elif k == '--rx-plan':
            a['rx_plan'] = os.path.basename(v)
        elif k == '--boots':
            a['boots'] = max(1, int(v))
        else:
            print('polari-c3-run: unknown option %s' % k)
            return 2
        i += 2
    return serve(a) if a['serve'] else scenario(a)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
