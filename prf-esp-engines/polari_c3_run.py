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

  TWIN mode (a long-lived container, `pol board twin c3 up`):
    polari-c3-run flash_image.bin --serve PORT [--trace FILE]
      UART0 on TCP :PORT (server, nowait — the host's pty pump dials it), UART1 to FILE, -icount shift=auto,sleep=on so
      virtual time tracks the wall clock (the bridge sees ~real time); runs until stopped.
"""
import json
import os
import shutil
import signal
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


def scenario(a):
    patch(a['image'], 'run.bin', a['params'], a['offset'])
    for f in ('uart0.bin', 'trace.log'):
        if os.path.exists(f):
            os.remove(f)
    argv = base_argv('run.bin', a['icount'], False) + ['-serial', 'file:uart0.bin', '-serial', 'file:trace.log']
    t0 = time.perf_counter()
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ended, why = False, 'max-wall'
    while time.perf_counter() - t0 < a['max_wall']:
        if p.poll() is not None:
            why = 'qemu exited (rc %s)' % p.returncode
            break
        try:
            with open('trace.log', 'rb') as f:
                f.seek(max(0, os.path.getsize('trace.log') - 4096))
                if ('\n' + a['until']).encode() in b'\n' + f.read():
                    ended, why = True, 'saw %s' % a['until']
                    break
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
    _, status, ru = os.wait4(p.pid, 0)
    wall = time.perf_counter() - t0
    out = p.stdout.read().decode('utf-8', 'replace') if p.stdout else ''
    os.remove('run.bin')
    lines = open('trace.log', 'rb').read().decode('utf-8', 'replace').count('\n') if os.path.exists('trace.log') else 0
    res = {'ended': ended, 'why': why, 'wall_s': round(wall, 3), 'cpu_s': round(ru.ru_utime + ru.ru_stime, 3),
           'peak_rss_mb': round(ru.ru_maxrss / 1024.0, 1), 'argv': argv, 'qemu_version': qemu_version(), 'icount_shift': a['icount'],
           'uart0_bytes': os.path.getsize('uart0.bin') if os.path.exists('uart0.bin') else 0, 'trace_lines': lines,
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
         'trace': '/tmp/trace.log'}
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
        else:
            print('polari-c3-run: unknown option %s' % k)
            return 2
        i += 2
    return serve(a) if a['serve'] else scenario(a)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
