#!/usr/bin/env python3
"""The twin door in front of the ESP worker (c3g-4, D-c3g-1 A): the board worker's ONE door module (twin_door.py) with this image's own `websockets`,
the ESP worker's own app (served on loopback), a C3 session from the ONE sessions module (SESSIONS_KIND=c3) and test_c3_twin's made-up QEMU. No ESP tool, no
firmware, no network beyond this machine's loopback.

What a caller on another device does through `wss://twin.esp-engines.isle`: the token as the first frame, then the twin's control line, one JSON line per
frame. A wrong token or an unknown session is refused with the door's close code and never reaches the twin. Run: python3 test_c3_door.py"""
import asyncio
import json
import logging
import os
import socket
import sys
import threading
import time
import unittest
import urllib.request
import wsgiref.simple_server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.join(HERE, '..', 'prf-board-engines')]      # the tree; in the image twin_door.py sits beside this file
import test_c3_sessions as T      # noqa: E402  (its setup: the twin, the runner and the made-up QEMU laid out as in the image)


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Quiet(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, *a):
        pass


def setUpModule():
    global door, W, D, httpd
    T.setUpModule()
    logging.getLogger('websockets.server').setLevel(logging.CRITICAL)      # /health is plain HTTP: the library logs it as a failed handshake
    import twin_door as door
    wport, dport = _free_port(), _free_port()
    httpd = wsgiref.simple_server.make_server('127.0.0.1', wport, T.svc.app, handler_class=_Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    W = 'http://127.0.0.1:%d' % wport
    door.WORKER, door.TARGET, door.TOKEN_WAIT_S = W, '127.0.0.1', 2
    up = threading.Event()
    threading.Thread(target=lambda: asyncio.run(door.main('127.0.0.1', dport, ready=lambda s: up.set())), daemon=True).start()
    if not up.wait(5):
        raise RuntimeError('the door did not start')
    D = 'ws://127.0.0.1:%d' % dport


def tearDownModule():
    httpd.shutdown()
    T.tearDownModule()


class C3Door(unittest.TestCase):
    def start(self):
        r = T.client.simulate_post('/sessions', json={'image_b64': T.IMAGE, 'layout': '{}', 'wires': '0-1', 'board': 'esp32-c3-devkitm-1', 'ttl_s': 60})
        self.assertEqual(r.status_code, 200, r.text)
        self.addCleanup(T.client.simulate_delete, '/sessions/%s' % r.json['id'])
        return r.json

    def closed_with(self, path, first):
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect
        with connect(D + path, open_timeout=5) as ws:
            ws.send(first)
            try:
                ws.recv(timeout=5)
            except ConnectionClosed as e:
                return e.rcvd.code if e.rcvd else None
        return 'open'

    def test_this_images_websockets_is_one_the_door_runs_on(self):
        import websockets
        self.assertGreaterEqual(int(websockets.__version__.split('.')[0]), 13, websockets.__version__)

    def test_the_door_says_healthy_when_it_reaches_the_esp_worker(self):
        with urllib.request.urlopen(D.replace('ws://', 'http://') + '/health', timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_with_the_token_first_the_c3_twins_control_line_answers_through_the_door(self):
        from websockets.sync.client import connect
        s = self.start()
        with connect(D + '/sessions/%s/ctl' % s['id'], open_timeout=5) as ws:
            ws.send(json.dumps({'token': s['token']}))
            self.assertEqual(json.loads(ws.recv(timeout=10)), {'t': 'ack', 'of': 'token'})
            ws.send(json.dumps({'status': 1}))
            t0, st = time.time(), None
            while time.time() - t0 < 10 and st is None:
                m = json.loads(ws.recv(timeout=10))
                if m.get('t') == 'status':
                    st = m
            self.assertIsNotNone(st, 'no status line came back through the door')
            self.assertIn('GPIO0', st.get('pads', {}), st)

    def test_a_wrong_token_is_refused_by_the_door_and_never_reaches_the_twin(self):
        s = self.start()
        self.assertEqual(self.closed_with('/sessions/%s/ctl' % s['id'], json.dumps({'token': 'x' * 32})), 4403)
        from test_c3_twin import Line
        c = Line(s['ports']['ctl'])                                  # the twin still takes the right token: it never saw the wrong one
        c.send({'token': s['token']})
        self.assertEqual(c.get(), {'t': 'ack', 'of': 'token'})

    def test_an_unknown_session_is_refused_by_the_door(self):
        self.assertEqual(self.closed_with('/sessions/abcdef012345/ctl', json.dumps({'token': 'x' * 32})), 4404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
