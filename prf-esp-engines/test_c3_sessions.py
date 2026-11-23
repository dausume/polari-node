#!/usr/bin/env python3
"""Twin sessions on the ESP worker (c3g-2): the worker's own app (falcon's test client, no gunicorn), the ONE sessions module with SESSIONS_KIND=c3, the real
polari-c3-twin, and test_c3_twin's made-up QEMU in place of the pin-model binary. No ESP tool, no firmware, no network beyond this machine's loopback.

The twin and the runner are laid out as in the image (polari-c3-twin and polari-c3-run side by side in one folder), so the sessions module finds a live twin
by its installed name. Run: python3 test_c3_sessions.py

With the pin-model QEMU installed AND C3_TWIN_IMAGE / C3_TWIN_LAYOUT naming a chip-HAL light build (see test_c3_twin.py), the run is the REAL one instead:
the worker starts a session from the posted image and two presses over the session's control port change the light twice. The made-up half is then skipped in words."""
import base64
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.join(HERE, '..', 'prf-board-engines')]      # the tree; in the image both modules sit beside this file
from test_c3_twin import FAKE_QEMU, Line, two_presses      # noqa: E402

REAL = bool(os.environ.get('C3_TWIN_IMAGE') and os.environ.get('C3_TWIN_LAYOUT') and shutil.which('qemu-system-riscv32-polari'))

IMAGE = base64.b64encode(b'\xe9' + b'\x00' * 8191).decode()
TMP = tempfile.mkdtemp(prefix='c3sessions-test-')


def _beside(tree_name, installed):
    p = os.path.join(HERE, tree_name)
    return p if os.path.exists(p) else shutil.which(installed)


def _port_run(n):
    """The first of n neighbouring ports nothing listens on right now."""
    for base in range(39832, 60000, 97):
        try:
            for p in range(base, base + n):
                s = socket.socket()
                s.bind(('127.0.0.1', p))
                s.close()
            return base
        except OSError:
            continue
    raise RuntimeError('no run of %d free ports' % n)


def setUpModule():
    global svc, bs, client
    bindir = os.path.join(TMP, 'bin')
    os.makedirs(bindir)
    for src, name in ((_beside('polari_c3_twin.py', 'polari-c3-twin'), 'polari-c3-twin'), (_beside('polari_c3_run.py', 'polari-c3-run'), 'polari-c3-run')):
        shutil.copy(src, os.path.join(bindir, name))
    if not REAL:
        with open(os.path.join(bindir, 'qemu-system-riscv32-polari'), 'w') as f:
            f.write(FAKE_QEMU)
    for name in os.listdir(bindir):
        os.chmod(os.path.join(bindir, name), 0o755)
    base = _port_run(8)
    os.environ.update(SESSIONS_KIND='c3', SESSIONS_DIR=os.path.join(TMP, 'sessions'), SESSIONS_TWIN_BIN=os.path.join(bindir, 'polari-c3-twin'),
                      SESSION_PORTS='%d-%d' % (base, base + 7), PATH=bindir + os.pathsep + os.environ.get('PATH', ''))
    os.environ.pop('TWIN_DOOR_URL', None)
    os.environ.pop('SESSION_PORTS_RESERVED', None)      # the worker's own default is under test
    import esp_engines_service as svc
    import board_sessions as bs
    from falcon import testing
    client = testing.TestClient(svc.app)


def tearDownModule():
    for s in client.simulate_get('/sessions').json.get('sessions', []):
        client.simulate_delete('/sessions/%s' % s['id'])
    shutil.rmtree(TMP, ignore_errors=True)


def twins_running():
    """Command lines of every process started from this test's folder (twins and their QEMU)."""
    out = []
    for pid in os.listdir('/proc'):
        if pid.isdigit():
            try:
                with open('/proc/%s/cmdline' % pid, 'rb') as f:
                    cmd = f.read().replace(b'\0', b' ').decode('utf-8', 'replace')
            except OSError:
                continue
            if TMP in cmd:
                out.append(cmd)
    return out


def gone(wait_s=5.0):
    t0 = time.time()
    while twins_running() and time.time() - t0 < wait_s:
        time.sleep(0.05)
    return twins_running()


@unittest.skipIf(REAL, 'this run is the real one: the pin-model QEMU and a real image are named')
class C3Sessions(unittest.TestCase):
    def start(self, **over):
        r = client.simulate_post('/sessions', json=dict({'image_b64': IMAGE, 'layout': '{}', 'wires': '0-1', 'board': 'esp32-c3-devkitm-1', 'ttl_s': 60}, **over))
        if r.status_code == 200:
            self.addCleanup(client.simulate_delete, '/sessions/%s' % r.json['id'])
        return r

    def left(self):
        return sorted(os.listdir(bs.DIR)) if os.path.isdir(bs.DIR) else []

    def test_the_capability_says_this_worker_runs_c3_twins_and_keeps_its_own_ports(self):
        cap = client.simulate_get('/capability').json
        self.assertEqual((cap['sessions']['kind'], cap['sessions']['available']), ('c3', True))
        self.assertNotIn('transports', cap)                       # no door configured: callers keep plain TCP
        self.assertTrue({9850, 9851} <= set(cap['sessions']['reserved_ports']), cap['sessions'])      # this worker's port and the runner's serve mode
        os.environ['TWIN_DOOR_URL'] = 'wss://twin.esp-engines.isle'
        try:
            self.assertEqual(client.simulate_get('/capability').json['transports']['ctl'], 'wss://twin.esp-engines.isle/sessions/{id}/ctl')
        finally:
            del os.environ['TWIN_DOOR_URL']

    def test_a_session_starts_the_twin_and_a_press_over_its_control_port_lights_the_led(self):
        r = self.start()
        self.assertEqual(r.status_code, 200, r.text)
        s = r.json
        self.assertEqual((s['kind'], s['mcu'], s['board'], s['state'], s['hex_bytes'], s['wires']), ('c3', 'esp32c3', 'esp32-c3-devkitm-1', 'running', 8192, '0-1'))
        self.assertEqual(s['ready']['pads'], ['GPIO0', 'GPIO1', 'GPIO3', 'GPIO9'])
        self.assertEqual(sorted(s['ports']), ['ctl', 'tcp'])
        work = os.path.join(bs.DIR, s['id'] + '.d')
        self.assertEqual(os.stat(work).st_mode & 0o777, 0o700)     # the pin channel lives here, never on the network
        c = Line(s['ports']['ctl'])
        self.addCleanup(c.close)
        self.assertEqual(c.ask({'token': s['token']}), {'t': 'ack', 'of': 'token'})
        self.assertEqual(c.ask({'watch': ['GPIO0']})['t'], 'ack')
        c.send({'set': 'GPIO3', 'level': 0})
        got = [c.get(), c.get()]
        self.assertIn({'t': 'pin', 'pin': 'GPIO0', 'level': 1, 'z': False, 'ns': 1000000}, got, got)
        time.sleep(0.7)
        one = client.simulate_get('/sessions/%s' % s['id']).json
        self.assertEqual(one['status']['pads']['GPIO0'], '1')
        self.assertNotIn('token', one)
        self.assertNotIn(s['token'], json.dumps(one))
        with open(os.path.join(bs.DIR, s['id'] + '.out')) as f:
            self.assertNotIn(s['token'], f.read())

    def test_the_doors_question_is_answered_as_on_the_board_worker(self):
        s = self.start().json
        ok = client.simulate_post('/sessions/%s/check' % s['id'], json={'token': s['token']})
        self.assertEqual((ok.status_code, ok.json['ports']), (200, s['ports']))
        self.assertNotIn(s['token'], ok.text)
        self.assertEqual(client.simulate_post('/sessions/%s/check' % s['id'], json={'token': 'x' * 32}).status_code, 403)
        self.assertEqual(client.simulate_post('/sessions/abcdef012345/check', json={'token': s['token']}).status_code, 404)

    def test_stopping_a_session_takes_the_twin_and_its_qemu_and_leaves_no_file(self):
        s = self.start().json
        self.assertEqual(len(twins_running()), 2, twins_running())            # the twin and its QEMU
        self.assertTrue(client.simulate_delete('/sessions/%s' % s['id']).json['stopped'])
        self.assertEqual(gone(), [])
        self.assertEqual([n for n in self.left() if n.startswith(s['id'])], [])

    def test_a_session_past_its_time_is_ended_with_the_reason_and_both_processes_go(self):
        s = self.start(ttl_s=10).json
        rec = bs._load(s['id'])
        rec['expires'] = time.time() - 1
        bs._save(rec)
        listed = [x for x in client.simulate_get('/sessions').json['sessions'] if x['id'] == s['id']]
        self.assertEqual([x['state'] for x in listed], ['ended'], listed)
        self.assertTrue(listed[0]['end_reason'])
        self.assertEqual(gone(), [])
        self.assertEqual(client.simulate_post('/sessions/%s/check' % s['id'], json={'token': s['token']}).status_code, 410)

    def test_a_request_the_twin_cannot_run_is_refused_in_words_and_leaves_nothing(self):
        before = self.left()
        bad = {'no image': {'image_b64': ''}, 'not base64': {'image_b64': '***'}, 'not a flash image': {'image_b64': base64.b64encode(b'\x7fELF' * 2048).decode()},
               'no layout': {'layout': ''}, 'layout not JSON': {'layout': '{'}, 'wires': {'wires': 'GPIO0-GPIO1'}, 'free': {'free': True}}
        for what, over in bad.items():
            r = self.start(**over)
            self.assertEqual(r.status_code, 400, (what, r.text))
            self.assertGreater(len(r.json['error']), 20, what)
        self.assertEqual(self.left(), before)
        self.assertEqual(twins_running(), [])
        hexy = self.start(hex_b64=base64.b64encode(b':00000001FF\n').decode(), image_b64='')
        self.assertIn('image_b64', hexy.json['error'])            # an AVR request to this worker is told what this worker takes

    def test_a_twin_that_does_not_come_up_is_reported_with_its_own_words_and_cleaned_up(self):
        before = self.left()
        r = self.start(wires='8-9')
        self.assertEqual(r.status_code, 500, r.text)
        self.assertIn('this board does not start', r.json['error'])
        self.assertEqual(self.left(), before)
        self.assertEqual(gone(), [])


@unittest.skipUnless(REAL, 'needs the pin-model QEMU on PATH and C3_TWIN_IMAGE / C3_TWIN_LAYOUT (a chip-HAL light build)')
class RealQemu(unittest.TestCase):
    def test_the_worker_starts_the_real_light_and_two_presses_change_it_twice(self):
        button, led = 'GPIO' + os.environ.get('C3_TWIN_BUTTON', '3'), 'GPIO' + os.environ.get('C3_TWIN_LED', '0')
        with open(os.environ['C3_TWIN_IMAGE'], 'rb') as f:
            image = base64.b64encode(f.read()).decode()
        with open(os.environ['C3_TWIN_LAYOUT']) as f:
            layout = f.read()
        t0 = time.time()
        r = client.simulate_post('/sessions', json={'image_b64': image, 'layout': layout, 'wires': os.environ.get('C3_TWIN_WIRES', '0-1'), 'board': 'esp32-c3-devkitm-1', 'ttl_s': 120})
        took = time.time() - t0
        self.assertEqual(r.status_code, 200, r.text[:600])
        s = r.json
        self.addCleanup(client.simulate_delete, '/sessions/%s' % s['id'])
        self.assertIn(button, s['ready']['pads'])
        c = Line(s['ports']['ctl'])
        self.addCleanup(c.close)
        self.assertEqual(c.ask({'token': s['token']}), {'t': 'ack', 'of': 'token'})
        before, seen = two_presses(self, c, button, led, lambda: client.simulate_get('/sessions/%s' % s['id']).json.get('output_tail', ''))
        rss = 0
        for pid in os.listdir('/proc'):                              # what one session holds in memory (the twin and its QEMU), for the cost record
            try:
                with open('/proc/%s/cmdline' % pid, 'rb') as f:
                    mine = TMP.encode() in f.read()
                if mine:
                    with open('/proc/%s/status' % pid) as f:
                        rss += sum(int(ln.split()[1]) for ln in f if ln.startswith('VmRSS:'))
            except (OSError, ValueError):
                continue
        c.close()
        self.assertTrue(client.simulate_delete('/sessions/%s' % s['id']).json['stopped'])
        self.assertEqual(gone(), [])
        print('\nreal session: ready %.1f s after the request (%d bytes posted); %s was %s, two presses on %s gave %s; one session held %d MiB; qemu %s'
              % (took, len(image), led, before, button, seen, rss // 1024, s['ready']['qemu_version']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
