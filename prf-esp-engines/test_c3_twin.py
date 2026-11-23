#!/usr/bin/env python3
"""polari-c3-twin's control line (c3g-1). Two halves:

  made-up QEMU   a stand-in for the pin-model binary that speaks the pin channel (levels on connect, `<pad> 0|1|z` in, `@ns pad level` out) and "runs" a
                 light: a press on pad 3 toggles pad 0 and its wired pad 1. No ESP tool, no network beyond this machine's loopback. Runs anywhere.
  the real one   only where the pin-model QEMU is installed AND the caller names a chip-HAL light image and a layout:
                 C3_TWIN_IMAGE=flash_image.bin C3_TWIN_LAYOUT=layout.json [C3_TWIN_WIRES=0-1 C3_TWIN_BUTTON=3 C3_TWIN_LED=0]. Skipped in words otherwise.

Run: python3 test_c3_twin.py"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TWIN = os.path.join(HERE, 'polari_c3_twin.py')      # the tree; in the image the twin is installed as polari-c3-twin
if not os.path.exists(TWIN):
    TWIN = shutil.which('polari-c3-twin') or TWIN
TOKEN = 'test-token-0123456789'

FAKE_QEMU = r'''#!/usr/bin/env python3
import os, socket, sys
if '--version' in sys.argv:
    print('made-up QEMU for test_c3_twin'); sys.exit(0)
path = [a for a in sys.argv if a.startswith('socket,path=')][0].split('path=')[1].split(',')[0]
wired = any(a == 'polari-block.wires=0-1' for a in sys.argv)
if any(a == 'polari-block.wires=8-9' for a in sys.argv):
    print('made-up QEMU: this board does not start'); sys.exit(4)
srv = socket.socket(socket.AF_UNIX); srv.bind(path); srv.listen(1)
c, _ = srv.accept()
pads, ns = {0: '0', 1: '0' if wired else 'z', 3: '1', 9: 'z'}, 0
c.sendall(''.join('@0 %d %s\n' % (p, pads[p]) for p in sorted(pads)).encode())
def put(p, lv):
    if pads[p] != lv:
        pads[p] = lv
        c.sendall(('@%d %d %s\n' % (ns, p, lv)).encode())
buf = b''
while True:
    d = c.recv(4096)
    if not d:
        break
    buf += d
    while b'\n' in buf:
        line, _, buf = buf.partition(b'\n')
        p, lv = line.decode().split()
        p, was = int(p), pads[int(p)]
        ns += 1000000
        if p == 9 and lv == '0':
            print('made-up QEMU: asked to exit'); sys.exit(3)
        put(p, lv)
        if p == 3 and lv == '0' and was != '0':
            led = '1' if pads[0] == '0' else '0'
            put(0, led)
            if wired:
                put(1, led)
'''


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Line:
    """One control client: JSON lines both ways."""
    def __init__(self, port):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=5)
        self.buf = b''

    def send(self, obj):
        self.s.sendall((json.dumps(obj) + '\n').encode())

    def get(self, timeout=5.0):
        """The next line as an object; None on a timeout; 'closed' when the twin hung up."""
        self.s.settimeout(timeout)
        while b'\n' not in self.buf:
            try:
                d = self.s.recv(4096)
            except socket.timeout:
                return None
            if not d:
                return 'closed'
            self.buf += d
        raw, _, self.buf = self.buf.partition(b'\n')
        return json.loads(raw)

    def ask(self, obj, timeout=5.0):
        self.send(obj)
        return self.get(timeout)

    def close(self):
        self.s.close()


class Up:
    """A twin process with its ports; `out()` is everything it printed so far."""
    def __init__(self, image, layout, wires='0-1', token=TOKEN, path=None):
        self.tcp, self.ctl = free_port(), free_port()
        self.log = tempfile.NamedTemporaryFile(prefix='c3twin-test-', suffix='.out', delete=False)
        env = dict(os.environ, POLARI_TWIN_CTL_TOKEN=token) if token else {k: v for k, v in os.environ.items() if k != 'POLARI_TWIN_CTL_TOKEN'}
        if path:
            env['PATH'] = path + os.pathsep + env.get('PATH', '')
        self.p = subprocess.Popen([sys.executable, TWIN, '--image', image, '--layout', layout, '--wires', wires, '--tcp', str(self.tcp), '--ctl', str(self.ctl),
                                   '--status-ms', '200'], stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)

    def out(self):
        with open(self.log.name) as f:
            return f.read()

    def lines(self, kind):
        return [json.loads(ln) for ln in self.out().splitlines() if ln.startswith('{"t":"%s"' % kind)]

    def ready(self, wait_s=30.0):
        t0 = time.time()
        while time.time() - t0 < wait_s and self.p.poll() is None and not self.lines('ready'):
            time.sleep(0.05)
        return self.lines('ready')[0] if self.lines('ready') else None

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
        self.log.close()
        os.unlink(self.log.name)


def two_presses(case, c, button, led, tail=lambda: ''):
    """Over an authed control line: watch the LED pad, let the firmware boot, press and let go twice. The light must change twice. -> (level before, events)."""
    case.assertEqual(c.ask({'watch': [led]})['of'], 'watch')
    time.sleep(float(os.environ.get('C3_TWIN_BOOT_S', '3')))                         # the firmware boots and sets its pads up
    before = c.ask({'status': 1})['pads'][led]
    seen = []
    for _ in range(2):
        c.send({'set': button, 'level': 0})
        time.sleep(0.2)
        c.send({'set': button, 'level': 'z'})
        acks, ev, t0 = 0, None, time.time()
        while time.time() - t0 < 8 and (ev is None or acks < 2):                     # the two acks and the LED's event, in whatever order they come
            m = c.get(timeout=1)
            if isinstance(m, dict) and m.get('t') == 'pin':
                ev = m
            elif isinstance(m, dict) and m.get('t') == 'ack':
                acks += 1
            elif m is not None:
                case.fail('%r after a press: %s' % (m, tail()))
        case.assertEqual(acks, 2)
        case.assertIsNotNone(ev, 'no event from the LED pad after a press: %s' % tail())
        seen.append((ev['pin'], ev['level']))
        time.sleep(0.3)
    flip = {'0': 1, '1': 0}[before]
    case.assertEqual(seen, [(led, flip), (led, 1 - flip)])
    return before, seen


class MadeUpQemu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix='c3twin-fake-')
        q = os.path.join(cls.dir, 'qemu-system-riscv32-polari')
        cls.image, cls.layout = os.path.join(cls.dir, 'flash_image.bin'), os.path.join(cls.dir, 'layout.json')
        for path, data in ((q, FAKE_QEMU.encode()), (cls.image, b'\xff' * 64), (cls.layout, b'{}')):
            with open(path, 'wb') as f:
                f.write(data)
        os.chmod(q, 0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def up(self, **kw):
        t = Up(self.image, self.layout, path=self.dir, **kw)
        self.addCleanup(t.stop)
        self.assertIsNotNone(t.ready(), t.out())
        return t

    def authed(self, t):
        c = Line(t.ctl)
        self.addCleanup(c.close)
        self.assertEqual(c.ask({'token': TOKEN}), {'t': 'ack', 'of': 'token'})
        return c

    def test_ready_names_the_pads_the_device_reported_and_status_lines_follow(self):
        t = self.up()
        r = t.ready()
        self.assertEqual((r['chip'], r['tcp'], r['ctl'], r['pads'], r['token']), ('esp32c3', t.tcp, t.ctl, ['GPIO0', 'GPIO1', 'GPIO3', 'GPIO9'], True))
        time.sleep(0.6)
        st = t.lines('status')
        self.assertGreaterEqual(len(st), 2)
        self.assertEqual(st[-1]['pads'], {'GPIO0': '0', 'GPIO1': '0', 'GPIO3': '1', 'GPIO9': 'z'})
        self.assertNotIn(TOKEN, t.out())

    def test_a_press_on_the_button_pad_comes_back_as_the_led_pad_and_its_wired_pad(self):
        t = self.up()
        c = self.authed(t)
        self.assertEqual(c.ask({'watch': ['GPIO0', 'GPIO1']}), {'t': 'ack', 'of': 'watch', 'watch': ['GPIO0', 'GPIO1']})
        self.assertEqual(c.ask({'set': 'GPIO3', 'level': 0}), {'t': 'ack', 'of': 'set', 'pin': 'GPIO3', 'level': 0, 'z': False})
        got = [c.get(), c.get()]
        self.assertEqual([(e['t'], e['pin'], e['level']) for e in got], [('pin', 'GPIO0', 1), ('pin', 'GPIO1', 1)])
        self.assertTrue(all(e['ns'] > 0 and e['z'] is False for e in got))
        self.assertEqual(c.ask({'set': 'GPIO3', 'level': 'z'})['z'], True)           # let go: the button pad is not watched, so nothing but the ack
        self.assertEqual(c.ask({'set': 'GPIO3', 'level': 0})['of'], 'set')           # the second press: the light goes out
        self.assertEqual([(e['pin'], e['level']) for e in (c.get(), c.get())], [('GPIO0', 0), ('GPIO1', 0)])
        st = c.ask({'status': 1})
        self.assertEqual((st['t'], st['attached'], st['watch'], st['pads']['GPIO3']), ('status', True, ['GPIO0', 'GPIO1'], '0'))

    def test_a_pad_that_is_not_watched_sends_no_event(self):
        t = self.up()
        c = self.authed(t)
        c.ask({'watch': ['GPIO3']})
        c.ask({'set': 'GPIO3', 'level': 0})                                         # GPIO0 and GPIO1 change; neither is watched
        ev = c.get()
        self.assertEqual((ev['t'], ev['pin'], ev['level']), ('pin', 'GPIO3', 0))     # a watched pad driven from outside is an event too, as on the AVR twin
        self.assertIsNone(c.get(timeout=0.5))

    def test_what_this_twin_cannot_do_is_refused_in_words_and_the_line_stays(self):
        t = self.up()
        c = self.authed(t)
        for cmd, word in (({'set': 'PD2', 'level': 0}, 'not a pad'), ({'set': 'GPIO7', 'level': 0}, 'not a pad'), ({'set': 'GPIO3', 'level': 2}, '"level"'),
                          ({'watch': ['GPIO0', 'GPIO42']}, 'not a pad'), ({'watch': 'GPIO0'}, 'list'), ({'run': False}, 'no pause'), ({'reset': 1}, 'no reset'),
                          ({'adc': 0, 'mv': 750}, 'no ADC'), ({'hello': 1}, 'no command')):
            r = c.ask(cmd)
            self.assertEqual(r['t'], 'refused', cmd)
            self.assertIn(word, r['why'], cmd)
        c.s.sendall(b'not json\n')
        self.assertIn('JSON', c.get()['why'])
        self.assertEqual(c.ask({'run': True}), {'t': 'ack', 'of': 'run', 'run': True})
        self.assertEqual(c.ask({'status': 1})['pads']['GPIO0'], '0')                # no refused command changed a pad

    def test_a_wrong_token_or_none_closes_the_line_and_never_reaches_a_pad(self):
        t = self.up()
        for first in ({'token': 'wrong-token-000'}, {'set': 'GPIO3', 'level': 0}):
            c = Line(t.ctl)
            self.assertEqual(c.ask(first), {'t': 'refused', 'why': 'wrong token'})
            self.assertEqual(c.get(), 'closed')
            c.close()
        c = self.authed(t)                                                           # the line is free again for the right caller
        self.assertEqual(c.ask({'status': 1})['pads']['GPIO3'], '1')

    def test_one_client_at_a_time_and_the_next_one_after_it_left(self):
        t = self.up()
        a = self.authed(t)
        b = Line(t.ctl)
        self.assertIn('one at a time', b.get()['why'])
        self.assertEqual(b.get(), 'closed')
        b.close()
        self.assertEqual(a.ask({'status': 1})['t'], 'status')
        a.close()
        time.sleep(0.3)
        c = self.authed(t)
        self.assertEqual(c.ask({'status': 1})['watch'], [])                          # the watch list left with the client that set it

    def test_without_a_token_in_the_environment_the_first_line_is_a_command(self):
        t = self.up(token='')
        self.assertFalse(t.ready()['token'])
        c = Line(t.ctl)
        self.addCleanup(c.close)
        self.assertEqual(c.ask({'status': 1})['t'], 'status')

    def test_when_qemu_exits_the_twin_says_so_and_ends(self):
        t = self.up()
        c = self.authed(t)
        c.send({'set': 'GPIO9', 'level': 0})
        t.p.wait(timeout=10)
        self.assertNotEqual(t.p.returncode, 0)
        self.assertIn('asked to exit', t.out())

    def test_a_missing_image_and_a_missing_engine_are_said_before_anything_starts(self):
        t = Up(os.path.join(self.dir, 'nope.bin'), self.layout, path=self.dir)
        self.addCleanup(t.stop)
        self.assertEqual(t.p.wait(timeout=10), 2)
        self.assertIn('is not there', t.out())
        if not shutil.which('qemu-system-riscv32-polari'):                           # only where the real one is not on PATH
            t = Up(self.image, self.layout)
            self.addCleanup(t.stop)
            self.assertEqual(t.p.wait(timeout=10), 2)
            self.assertIn('--pins needs', t.out())


IMAGE, LAYOUT = os.environ.get('C3_TWIN_IMAGE', ''), os.environ.get('C3_TWIN_LAYOUT', '')
WHY_NOT = ('' if shutil.which('qemu-system-riscv32-polari') and IMAGE and LAYOUT
           else 'the real half needs the pin-model QEMU on PATH and C3_TWIN_IMAGE / C3_TWIN_LAYOUT (a chip-HAL light image and its layout)')


@unittest.skipIf(WHY_NOT, WHY_NOT)
class RealQemu(unittest.TestCase):
    def test_the_real_light_firmware_is_pressed_over_the_control_line(self):
        button, led = 'GPIO' + os.environ.get('C3_TWIN_BUTTON', '3'), 'GPIO' + os.environ.get('C3_TWIN_LED', '0')
        t = Up(IMAGE, LAYOUT, wires=os.environ.get('C3_TWIN_WIRES', '0-1'))
        self.addCleanup(t.stop)
        r = t.ready(wait_s=60)
        self.assertIsNotNone(r, t.out())
        self.assertIn(button, r['pads'])
        c = Line(t.ctl)
        self.addCleanup(c.close)
        self.assertEqual(c.ask({'token': TOKEN}), {'t': 'ack', 'of': 'token'})
        before, seen = two_presses(self, c, button, led, lambda: t.out()[-600:])
        print('\nreal light: %s was %s, two presses on %s gave %s; qemu %s' % (led, before, button, seen, r['qemu_version']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
