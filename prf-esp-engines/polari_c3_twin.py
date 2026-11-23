#!/usr/bin/env python3
"""
polari-c3-twin — c3g-1 (AI-Notes/plans/C3_SESSION_PLAN.md): ONE LIVE C3 TWIN BEHIND THE CONTROL LINE THE AVR TWIN SPEAKS.

    polari-c3-twin --image flash_image.bin --layout layout.json [--wires 0-1] --tcp PORT --ctl PORT [--status-ms 500] [--work DIR]

Boots the image in the pin-model QEMU (the build `polari-c3-run --pins` uses) in twin time (-icount shift=auto,sleep=on: virtual time tracks the wall clock) and
stays up until it is stopped. It is the C3's counterpart of `polari-avr-twin --hex .. --tcp .. --ctl ..`, so one sessions module and one door serve both chips:

  --tcp PORT   the firmware's UART0, raw bytes both ways (QEMU's own TCP server)
  --ctl PORT   the control line: JSON lines both ways, ONE client at a time (prf-board-engines/twin_ctl.h is the contract). The token comes through the
               environment (POLARI_TWIN_CTL_TOKEN), never argv; with one set, the first line must be {"token": "..."} and a wrong one closes the line.
        in     {"set":"GPIO3","level":0|1|"z"}   drive a pad from outside NOW, or let it go (z: the pulls and the chip decide)
               {"watch":["GPIO0"]}               replace the watch list; every level change of a watched pad is sent as an event
               {"status":1}                      one status line
               {"run":true}                      acknowledged (it runs); {"run":false}, {"reset":1} and {"adc":..} are REFUSED IN WORDS: this twin has no
                                                 pause, no reset and no ADC yet, and a silent yes would read as done
        out    {"t":"ack","of":..} · {"t":"pin","pin":"GPIO0","level":0|1|null,"z":bool,"ns":N} · {"t":"status",..} · {"t":"refused","why":".."}
               `ns` is QEMU's virtual time of the change (the AVR twin's `cycle` has no counterpart here, so none is sent).

QEMU's pin channel has no token and takes one client; here it is a UNIX socket in the twin's own folder (mode 0700), and this process is its one client - the
channel is never on the network. Pads are named GPIO<n>; which pads exist is what the device reports when the channel opens (the layout decides, not this file).
stdout: one {"t":"ready",..} line, then a {"t":"status",..} line every --status-ms (the sessions module reads both), and {"t":"ended",..} when QEMU exits.
"""
import argparse
import hmac
import importlib.machinery
import importlib.util
import json
import os
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

LEVELS = {'0': 0, '1': 1, 'z': None}
MAX_LINE = 4096


def _runner():
    """polari-c3-run as a module (the QEMU argv is its own, never typed twice): beside this file as .py in the tree, without the suffix in the image."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ('polari_c3_run.py', 'polari-c3-run'):
        p = os.path.join(here, name)
        if os.path.exists(p):
            sys.dont_write_bytecode = True
            loader = importlib.machinery.SourceFileLoader('polari_c3_run', p)
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader('polari_c3_run', loader))
            loader.exec_module(mod)
            return mod
    raise SystemExit('polari-c3-twin: polari-c3-run is not beside this file (%s)' % here)


def say(obj):
    print(json.dumps(obj, separators=(',', ':')), flush=True)


def pad_name(pad):
    return 'GPIO%d' % pad


def parse_pins(text):
    """The channel's complete lines -> [(ns, pad, '0'|'1'|'z')]."""
    out = []
    for line in text.split('\n'):
        p = line.split()
        if len(p) == 3 and p[0].startswith('@') and p[0][1:].isdigit() and p[1].isdigit() and p[2] in LEVELS:
            out.append((int(p[0][1:]), int(p[1]), p[2]))
    return out


class Twin:
    def __init__(self, pins, token, t0):
        self.pins, self.token, self.t0 = pins, token, t0
        self.pads, self.ns, self.watch = {}, 0, set()
        self.client, self.authed, self.inbuf, self.pinbuf = None, False, b'', b''
        self.refused_clients = self.dropped = 0

    # -- the pin channel (QEMU) --
    def take_pins(self, data):
        self.pinbuf += data
        text, _, rest = self.pinbuf.rpartition(b'\n')
        self.pinbuf = rest
        for ns, pad, lv in parse_pins(text.decode('ascii', 'replace')):
            self.ns = max(self.ns, ns)
            changed = self.pads.get(pad) != lv
            self.pads[pad] = lv
            if changed and self.authed and pad_name(pad) in self.watch:
                self.out({'t': 'pin', 'pin': pad_name(pad), 'level': LEVELS[lv], 'z': lv == 'z', 'ns': ns})

    # -- the control line --
    def out(self, obj):
        if self.client is None:
            return
        try:
            self.client.sendall((json.dumps(obj, separators=(',', ':')) + '\n').encode())
        except OSError:
            self.dropped += 1
            self.hang_up()

    def hang_up(self):
        if self.client is not None:
            try:
                self.client.close()
            except OSError:
                pass
        self.client, self.authed, self.inbuf, self.watch = None, False, b'', set()

    def status(self):
        return {'t': 'status', 'chip': 'esp32c3', 'ns': self.ns, 'wall_s': round(time.time() - self.t0, 1), 'running': True, 'attached': self.client is not None,
                'pads': {pad_name(p): self.pads[p] for p in sorted(self.pads)}, 'watch': sorted(self.watch), 'dropped': self.dropped}

    def _pad(self, name):
        """The pad number of a GPIO<n> name this layout has, or None after refusing."""
        s = str(name)
        if s.startswith('GPIO') and s[4:].isdigit() and int(s[4:]) in self.pads:
            return int(s[4:])
        self.out({'t': 'refused', 'why': '%s is not a pad of this layout (pads: %s)' % (s, ' '.join(pad_name(p) for p in sorted(self.pads)))})
        return None

    def line(self, raw):
        try:
            cmd = json.loads(raw)
            assert isinstance(cmd, dict)
        except Exception:
            return self.out({'t': 'refused', 'why': 'not a JSON object on one line'})
        if 'token' in cmd or not self.authed:
            if self.token is not None and not hmac.compare_digest(str(cmd.get('token') or '').encode(), self.token.encode()):
                self.out({'t': 'refused', 'why': 'wrong token'})
                return self.hang_up()
            self.authed = True
            if 'token' in cmd:
                self.out({'t': 'ack', 'of': 'token'})
                cmd = {k: v for k, v in cmd.items() if k != 'token'}
                if not cmd:
                    return
        did = 0
        if 'set' in cmd:
            did += 1
            lv = {0: '0', 1: '1', 'z': 'z'}.get(cmd.get('level') if not isinstance(cmd.get('level'), bool) else int(cmd['level']))
            pad = self._pad(cmd['set'])
            if pad is not None and lv is None:
                self.out({'t': 'refused', 'why': '"set" needs "level": 0 or 1, or "z" to let the pad go'})
            elif pad is not None:
                self.pins.sendall(('%d %s\n' % (pad, lv)).encode())
                self.out({'t': 'ack', 'of': 'set', 'pin': pad_name(pad), 'level': LEVELS[lv], 'z': lv == 'z'})
        if 'watch' in cmd:
            did += 1
            names = cmd['watch'] if isinstance(cmd['watch'], list) else None
            if names is None:
                self.out({'t': 'refused', 'why': '"watch" is a list of pad names'})
            elif all(self._pad(n) is not None for n in names):
                self.watch = {str(n) for n in names}
                self.out({'t': 'ack', 'of': 'watch', 'watch': sorted(self.watch)})
        if 'run' in cmd:
            did += 1
            if cmd['run'] is True:
                self.out({'t': 'ack', 'of': 'run', 'run': True})
            else:
                self.out({'t': 'refused', 'why': 'the C3 twin has no pause yet: QEMU keeps its own clock (stop the session instead)'})
        if 'reset' in cmd:
            did += 1
            self.out({'t': 'refused', 'why': 'the C3 twin has no reset yet (stop the session and start one for a fresh boot)'})
        if 'adc' in cmd:
            did += 1
            self.out({'t': 'refused', 'why': 'the C3 pin model holds levels only: it has no ADC yet'})
        if 'status' in cmd:
            did += 1
            self.out(self.status())
        if not did:
            self.out({'t': 'refused', 'why': 'no command in that line (set, watch, status, run)'})

    def take_ctl(self, data):
        self.inbuf += data
        if len(self.inbuf) > MAX_LINE and b'\n' not in self.inbuf:
            self.out({'t': 'refused', 'why': 'a line over %d bytes' % MAX_LINE})
            return self.hang_up()
        while b'\n' in self.inbuf and self.client is not None:
            raw, _, self.inbuf = self.inbuf.partition(b'\n')
            if raw.strip():
                self.line(raw.decode('utf-8', 'replace'))


def _tail(path, n=600):
    try:
        return open(path, 'rb').read()[-n:].decode('utf-8', 'replace').strip()
    except OSError:
        return ''


def _dial_pins(path, proc, log, wait_s=20.0):
    """The one client of QEMU's pin channel, with the levels it reports on connect read in."""
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if proc.poll() is not None:
            raise SystemExit('polari-c3-twin: QEMU exited (%s) before the pin channel opened: %s' % (proc.returncode, _tail(log) or 'no output'))
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(path)
            return s
        except OSError:
            s.close()
            time.sleep(0.05)
    raise SystemExit('polari-c3-twin: the pin channel did not open within %gs: %s' % (wait_s, _tail(log) or 'no output'))


def main(argv):
    ap = argparse.ArgumentParser(prog='polari-c3-twin')
    ap.add_argument('--image', required=True)
    ap.add_argument('--layout', required=True)
    ap.add_argument('--wires', default='')
    ap.add_argument('--tcp', type=int, required=True)
    ap.add_argument('--ctl', type=int, required=True)
    ap.add_argument('--status-ms', type=int, default=500)
    ap.add_argument('--work', default='')
    a = ap.parse_args(argv)
    R = _runner()
    work = a.work or tempfile.mkdtemp(prefix='c3twin-')
    os.makedirs(work, exist_ok=True)
    os.chmod(work, 0o700)
    img, layout, chan, log = (os.path.join(work, n) for n in ('flash.bin', 'layout.json', 'pins.sock', 'qemu.out'))
    for src, dst in ((a.image, img), (a.layout, layout)):      # QEMU writes its flash: the twin runs a copy, the caller's file stays as it was sent
        if not os.path.exists(src):
            print('polari-c3-twin: %s is not there' % src)
            return 2
        shutil.copy(src, dst)
    opts = {'pins': layout, 'pins_in': '', 'wires': a.wires}
    why = R.pins_refusal(opts)
    if why:
        print(why)
        return 2
    argv_q = (R.base_argv(img, 3, True, layout) + ['-serial', 'tcp::%d,server,nowait' % a.tcp, '-serial', 'file:%s' % os.path.join(work, 'trace.log')]
              + R.pins_argv(opts, 'socket,path=%s,server=on,wait=off' % chan))
    try:
        door = socket.create_server(('0.0.0.0', a.ctl))
    except OSError as e:
        print('polari-c3-twin: cannot listen on --ctl %d: %s' % (a.ctl, e))
        return 2
    out = open(log, 'wb')
    proc = subprocess.Popen(argv_q, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    out.close()

    def stop(*_):
        try:
            proc.kill()
        except OSError:
            pass
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        pins = _dial_pins(chan, proc, log)
        twin = Twin(pins, os.environ.get('POLARI_TWIN_CTL_TOKEN') or None, time.time())
        pins.settimeout(0.05)
        t_first, last = time.time(), None
        while time.time() - t_first < 5.0 and (last is None or time.time() - last < 0.3):     # the on-connect report: every pad's level
            try:
                d = pins.recv(65536)
            except socket.timeout:
                continue
            if not d:
                break
            twin.take_pins(d)
            last = time.time()
        if not twin.pads:
            raise SystemExit('polari-c3-twin: the pin channel reported no pads (a QEMU built before the on-connect report?): %s' % _tail(log))
        say({'t': 'ready', 'chip': 'esp32c3', 'tcp': a.tcp, 'ctl': a.ctl, 'realtime': 1, 'wires': a.wires, 'pads': [pad_name(p) for p in sorted(twin.pads)],
             'qemu_version': R.qemu_version(argv_q[0]), 'token': bool(twin.token)})
        sel = selectors.DefaultSelector()
        pins.setblocking(False)
        door.setblocking(False)
        sel.register(pins, selectors.EVENT_READ, 'pins')
        sel.register(door, selectors.EVENT_READ, 'door')
        next_status = time.time() + a.status_ms / 1000.0
        held = None                                            # the control client the selector holds; the twin may hang one up while it answers
        while True:
            for key, _ in sel.select(timeout=0.05):
                if key.data == 'pins':
                    d = pins.recv(65536)
                    if not d:
                        raise SystemExit('polari-c3-twin: QEMU closed the pin channel: %s' % _tail(log))
                    twin.take_pins(d)
                elif key.data == 'door':
                    c, _addr = door.accept()
                    if twin.client is not None:
                        twin.refused_clients += 1
                        try:
                            c.sendall(b'{"t":"refused","why":"another control client is attached (one at a time)"}\n')
                        except OSError:
                            pass
                        c.close()
                    else:
                        c.settimeout(2.0)
                        twin.client, twin.authed = c, twin.token is None
                        sel.register(c, selectors.EVENT_READ, 'ctl')
                        held = c
                elif key.data == 'ctl' and key.fileobj is twin.client:
                    try:
                        d = twin.client.recv(65536)
                    except OSError:
                        d = b''
                    if d:
                        twin.take_ctl(d)
                    else:
                        twin.hang_up()
            if held is not None and twin.client is not held:
                try:
                    sel.unregister(held)
                except (KeyError, ValueError):
                    pass
                held = None
            if proc.poll() is not None:
                say({'t': 'ended', 'why': 'QEMU exited (%s): %s' % (proc.returncode, _tail(log) or 'no output')})
                return 1
            if time.time() >= next_status:
                say(twin.status())
                next_status = time.time() + a.status_ms / 1000.0
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        if not a.work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
