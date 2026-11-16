"""twin_door_selftest - td-0 (TWIN_DOOR_PLAN.md §3/§3a): the door, the worker's token check and the transports block,
proved against the REAL worker app and the REAL door on loopback, with a stand-in twin that speaks the twin's two
port protocols (the image is not needed). Needs python3-falcon and websockets on the host.

    python3 twin_door_selftest.py

Not proved here (live steps): the agent's 60 s idle cut on a silent uart, the leaf certificate, the DNS name.
"""
import asyncio
import base64
import json
import logging
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wsgiref.simple_server

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_TWIN = r'''#!/usr/bin/env python3
# a stand-in for polari-avr-twin: --tcp P echoes bytes (one client), --ctl P speaks the control line (one client at a time)
import json, os, socket, sys, threading, time
a = sys.argv
tcp, ctl = int(a[a.index('--tcp') + 1]), int(a[a.index('--ctl') + 1])
token = os.environ.get('POLARI_TWIN_CTL_TOKEN') or None
log = open(os.environ['FAKE_TWIN_LOG'], 'a', buffering=1)
def listen(p, n):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(('127.0.0.1', p)); s.listen(n); return s
ls_u, ls_c = listen(tcp, 1), listen(ctl, 2)
def uart():
    while True:
        c, _ = ls_u.accept(); log.write('uart-accept\n')
        got = b''
        while True:
            d = c.recv(65536)
            if not d: break
            if not got: log.write('uart-first %s\n' % d[:12].hex())
            got += d; c.sendall(d)
        c.close()
busy = threading.Lock()
def ctl_client(c):
    if not busy.acquire(blocking=False):
        c.sendall(b'{"t":"refused","why":"another control client is attached (one at a time)"}\n'); c.close(); return
    try:
        log.write('ctl-accept\n'); authed = token is None; buf = b''
        f = c.makefile('rb')
        for raw in f:
            log.write('ctl-line %s\n' % raw.decode().rstrip('\n'))
            try: d = json.loads(raw)
            except ValueError: c.sendall(b'{"t":"refused","why":"not json"}\n'); continue
            if 'token' in d:
                if token and d['token'] != token: c.sendall(b'{"t":"refused","why":"the token is wrong"}\n'); break
                authed = True; c.sendall(b'{"t":"ack","of":"token"}\n'); continue
            if not authed: c.sendall(b'{"t":"refused","why":"token first"}\n'); break
            if 'status' in d: c.sendall(b'{"t":"status","cycles":42}\n')
            elif 'watch' in d: c.sendall(b'{"t":"ack","of":"watch"}\n{"t":"pin","pin":"B5","v":1}\n')
            else: c.sendall(b'{"t":"refused","why":"no command in that line"}\n')
    finally:
        c.close(); busy.release()
def ctl_loop():
    while True:
        c, _ = ls_c.accept(); threading.Thread(target=ctl_client, args=(c,), daemon=True).start()
threading.Thread(target=uart, daemon=True).start(); threading.Thread(target=ctl_loop, daemon=True).start()
print(json.dumps({'t': 'ready', 'tcp': tcp, 'ctl': ctl}, separators=(',', ':')), flush=True)
while True: time.sleep(60)
'''


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def run(check):
    from websockets.exceptions import ConnectionClosed, InvalidStatus
    from websockets.sync.client import connect
    tmp = tempfile.mkdtemp(prefix='twin-door-selftest-')
    twin = os.path.join(tmp, 'polari-avr-twin')
    open(twin, 'w').write(FAKE_TWIN)
    os.chmod(twin, os.stat(twin).st_mode | stat.S_IXUSR)
    twin_log = os.path.join(tmp, 'twin.log')
    open(twin_log, 'w').close()
    os.environ.update(SESSIONS_DIR=os.path.join(tmp, 'sessions'), SESSIONS_TWIN_BIN=twin, FAKE_TWIN_LOG=twin_log,
                      SESSION_PORTS='%d-%d' % (39832, 39863), SESSION_PORTS_RESERVED='')
    os.environ.pop('TWIN_DOOR_URL', None)
    sys.path.insert(0, HERE)
    import board_engines_service as svc
    import board_sessions as bs
    import twin_door as door

    class Quiet(wsgiref.simple_server.WSGIRequestHandler):
        def log_message(self, *a):
            pass
    wport, dport = free_port(), free_port()
    httpd = wsgiref.simple_server.make_server('127.0.0.1', wport, svc.app, handler_class=Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    W = 'http://127.0.0.1:%d' % wport
    door.WORKER, door.TARGET, door.TOKEN_WAIT_S, door.PING_S = W, '127.0.0.1', 1, 1
    pings = []

    class Pings(logging.Handler):
        def emit(self, rec):
            if 'PING' in rec.getMessage() and rec.getMessage().lstrip().startswith('>'):
                pings.append(time.time())
    lg = logging.getLogger('websockets.server')
    lg.setLevel(logging.DEBUG)
    lg.addHandler(Pings())
    lg.propagate = False
    door_log = []

    class Lines(logging.Handler):
        def emit(self, rec):
            door_log.append(rec.getMessage())
    dl = logging.getLogger('twin_door')
    dl.setLevel(logging.INFO)
    dl.addHandler(Lines())
    dl.propagate = False
    up = threading.Event()
    threading.Thread(target=lambda: asyncio.run(door.main('127.0.0.1', dport, ready=lambda s: up.set())), daemon=True).start()
    up.wait(5)
    D = 'ws://127.0.0.1:%d' % dport

    # ---- the transports block
    cap = json.loads(http('GET', W + '/capability')[1])
    check('td-0 CAPABILITY: a worker with no TWIN_DOOR_URL advertises no transports (callers keep plain TCP)', 'transports' not in cap, sorted(cap))
    os.environ['TWIN_DOOR_URL'] = 'wss://twin.board-engines.isle/'
    cap = json.loads(http('GET', W + '/capability')[1])
    check('td-0 CAPABILITY: with TWIN_DOOR_URL, transports is a top-level key with the two {id} URLs, beside sessions',
          cap.get('transports') == {'ctl': 'wss://twin.board-engines.isle/sessions/{id}/ctl', 'uart': 'wss://twin.board-engines.isle/sessions/{id}/uart'}
          and 'sessions' in cap, cap.get('transports'))
    bad = []
    for v in ('https://twin.board-engines.isle', 'wss://twin.board-engines.isle/x', 'twin.board-engines.isle', 'wss://a b'):
        os.environ['TWIN_DOOR_URL'] = v
        if 'transports' in json.loads(http('GET', W + '/capability')[1]):
            bad.append(v)
    os.environ.pop('TWIN_DOOR_URL')
    check('td-0 CAPABILITY: a TWIN_DOOR_URL that is not ws(s)://host[:port] advertises nothing (never a half-right URL)', not bad, bad)

    # ---- a session and the check route
    def start():
        st, raw = http('POST', W + '/sessions', {'hex_b64': base64.b64encode(b':00000001FF\n').decode(), 'ttl_s': 120})
        return st, json.loads(raw)
    st, s1 = start()
    sid, tok = s1.get('id'), s1.get('token')
    check('td-0 SESSION: the real worker starts a session on the stand-in twin (id, token, two ports)',
          st == 200 and sid and tok and set(s1.get('ports', {})) == {'tcp', 'ctl'}, s1)
    st, raw = http('POST', W + '/sessions/%s/check' % sid, {'token': tok})
    body = json.loads(raw)
    check('td-0 CHECK: the right token is 200 with the ports and the expiry, and the answer never repeats the token',
          st == 200 and body.get('ports') == s1['ports'] and body.get('expires') and tok not in raw.decode(), body)
    codes = [http('POST', W + '/sessions/%s/check' % sid, b)[0] for b in ({'token': tok + 'x'}, {'token': ''}, {}, {'token': 5}, [])]
    check('td-0 CHECK: a wrong, empty, missing or non-text token is 403', codes == [403] * 5, codes)
    check('td-0 CHECK: an unknown id is 404', http('POST', W + '/sessions/%s/check' % 'abcdef012345', {'token': tok})[0] == 404, '')
    st, s2 = start()
    http('DELETE', W + '/sessions/' + s2['id'])
    ended = dict(bs._load(sid), id='0e0e0e0e0e0e', ended=time.time(), end_reason='test')
    bs._save(ended)
    check('td-0 CHECK: an ended session is 410 even with the right token',
          http('POST', W + '/sessions/0e0e0e0e0e0e/check', {'token': tok})[0] == 410, '')

    # ---- the door before the upgrade
    check('td-0 DOOR HTTP: GET /health is 200 while the door reaches its worker', http('GET', 'http://127.0.0.1:%d/health' % dport)[0] == 200, '')
    door.WORKER = 'http://127.0.0.1:%d' % free_port()
    h503 = http('GET', 'http://127.0.0.1:%d/health' % dport)[0]
    door.WORKER = W
    check('td-0 DOOR HTTP: /health is 503 when the worker cannot be reached (a door without its worker is not healthy)', h503 == 503, h503)
    refused = []
    for p in ('/', '/sessions', '/sessions/%s' % sid, '/sessions/%s/pins' % sid, '/sessions/../x/ctl', '/sessions/%s/ctl/extra' % sid):
        try:
            connect(D + p, open_timeout=5).close()
            refused.append((p, 'opened'))
        except InvalidStatus as e:
            if e.response.status_code != 404:
                refused.append((p, e.response.status_code))
    check('td-0 DOOR HTTP: any path that is not /sessions/<id>/ctl or /uart is 404 before the upgrade', not refused, refused)

    def closed(ws, wait=5):
        """(close code, reason) once the door closes the socket."""
        try:
            while True:
                ws.recv(timeout=wait)
        except ConnectionClosed as e:
            return (e.rcvd.code, e.rcvd.reason) if e.rcvd else (None, 'no close frame')
        except TimeoutError:
            return ('timeout', '')

    def twin_lines():
        return open(twin_log).read().splitlines()

    # ---- refusals (none of these may reach the twin)
    before = len(twin_lines())
    ws = connect(D + '/sessions/%s/ctl' % sid)
    ws.send(json.dumps({'token': tok + 'x'}))
    c403 = closed(ws)
    ws = connect(D + '/sessions/%s/uart' % sid)
    ws.send(json.dumps({'token': 'nope-nope'}))
    u403 = closed(ws)
    ws = connect(D + '/sessions/%s/ctl' % 'abcdef012345')
    ws.send(json.dumps({'token': tok}))
    c404 = closed(ws)
    ws = connect(D + '/sessions/0e0e0e0e0e0e/ctl')
    ws.send(json.dumps({'token': tok}))
    c410 = closed(ws)
    ws = connect(D + '/sessions/%s/ctl' % sid)
    c401 = closed(ws)
    firsts = []
    for frame in (b'\x01\x02', 'not json', '{"tok":"x"}', '{"token":7}', '[]'):
        ws = connect(D + '/sessions/%s/uart' % sid)
        ws.send(frame)
        firsts.append(closed(ws)[0])
    check('td-0 REFUSE: wrong token is 4403 on ctl and on uart; unknown id 4404; ended 4410; no first frame in time 4401',
          (c403[0], u403[0], c404[0], c410[0], c401[0]) == (4403, 4403, 4404, 4410, 4401), (c403, u403, c404, c410, c401))
    check('td-0 REFUSE: a first frame that is binary, not JSON, without "token" or with a non-text token is 4400', firsts == [4400] * 5, firsts)
    check('td-0 REFUSE: none of those refusals opened a twin port (a wrong token never reaches the twin, which hangs up for good on one)',
          len(twin_lines()) == before, twin_lines()[before:])
    bs._remove('0e0e0e0e0e0e')

    # ---- the control line
    ws = connect(D + '/sessions/%s/ctl' % sid)
    ws.send(json.dumps({'token': tok}))
    ack = ws.recv(timeout=5)
    check('td-0 CTL: after the token frame the first frame back is the TWIN\'s own ack, as text, with no trailing newline',
          isinstance(ack, str) and json.loads(ack) == {'t': 'ack', 'of': 'token'} and not ack.endswith('\n'), ack)
    ws.send('{"status":1}')
    r1 = ws.recv(timeout=5)
    ws.send('{"status":1}\n')
    r2 = ws.recv(timeout=5)
    ws.send('{"watch":["B5"]}\r\n')
    r3, r4 = ws.recv(timeout=5), ws.recv(timeout=5)
    check('td-0 CTL: a command with or without its newline gets one reply frame; two twin lines sent together arrive as two frames (ack, then the pushed event)',
          json.loads(r1) == json.loads(r2) == {'t': 'status', 'cycles': 42} and json.loads(r3)['of'] == 'watch' and json.loads(r4)['t'] == 'pin'
          and not any(x.endswith('\n') for x in (r1, r2, r3, r4)), (r1, r2, r3, r4))
    got = [ln for ln in twin_lines() if ln.startswith('ctl-line')]
    check('td-0 CTL: the twin received exactly the caller\'s lines, one per frame (the token line first)',
          got[-4:] == ['ctl-line ' + json.dumps({'token': tok}), 'ctl-line {"status":1}', 'ctl-line {"status":1}', 'ctl-line {"watch":["B5"]}'], got[-4:])
    ws2 = connect(D + '/sessions/%s/ctl' % sid)
    ws2.send(json.dumps({'token': tok}))
    ack2 = ws2.recv(timeout=5)
    old = closed(ws)
    check('td-0 REPLACE: a second ctl connection closes the first with 4409 and gets the twin\'s ack (the twin takes one client: the old socket was closed before the new dial)',
          old[0] == 4409 and json.loads(ack2).get('t') == 'ack', (old, ack2))
    ws2.send(b'\x00')
    b1003 = closed(ws2)
    ws3 = connect(D + '/sessions/%s/ctl' % sid)
    ws3.send(json.dumps({'token': tok}))
    ws3.recv(timeout=5)
    ws3.send('{"status":1}\n{"status":1}')
    l1003 = closed(ws3)
    check('td-0 CTL: a binary frame on ctl, or two lines in one frame, closes with 1003', (b1003[0], l1003[0]) == (1003, 1003), (b1003, l1003))

    # ---- the serial stream
    ws = connect(D + '/sessions/%s/uart' % sid, max_size=None)
    ws.send(json.dumps({'token': tok}))
    small = b'\x7e\x00\n\r\xff{"token":"x"}\n\x00'
    ws.send(small)
    echo = b''
    while len(echo) < len(small):
        echo += ws.recv(timeout=5)
    first_seen = [ln for ln in twin_lines() if ln.startswith('uart-first')][-1]
    check('td-0 UART: bytes go both ways unchanged as binary frames (newlines, NUL and 0xFF kept); the token frame is consumed, never sent to the twin',
          echo == small and first_seen == 'uart-first ' + small[:12].hex(), (echo, first_seen))
    big = os.urandom(600 * 1024)
    ws.send(big)
    back = b''
    while len(back) < len(big):
        back += ws.recv(timeout=10)
    check('td-0 UART: 600 KiB in one frame comes back whole and in order', back == big, (len(back), len(big)))
    n0 = len(pings)
    time.sleep(3.5)
    ws.send(b'still here')
    alive = ws.recv(timeout=5)
    check('td-0 KEEPALIVE: on a silent uart the door sends pings at its interval (set to 1 s here, 20 s in service) and the stream stays up',
          len(pings) - n0 >= 2 and alive == b'still here', (len(pings) - n0, alive))
    wst = connect(D + '/sessions/%s/uart' % sid)
    wst.send(json.dumps({'token': tok}))
    rep = closed(ws)
    wst.send('text after the token')
    t1003 = closed(wst)
    check('td-0 UART: a newer uart connection replaces the old one (4409); a text frame after the token closes with 1003',
          (rep[0], t1003[0]) == (4409, 1003), (rep, t1003))

    # ---- the session ends under an open stream
    wc = connect(D + '/sessions/%s/ctl' % sid)
    wc.send(json.dumps({'token': tok}))
    wc.recv(timeout=5)
    wu = connect(D + '/sessions/%s/uart' % sid)
    wu.send(json.dumps({'token': tok}))
    wu.send(b'x')
    wu.recv(timeout=5)
    http('DELETE', W + '/sessions/' + sid)
    e1, e2 = closed(wc), closed(wu)
    wl = connect(D + '/sessions/%s/ctl' % sid)
    wl.send(json.dumps({'token': tok}))
    late = closed(wl)
    check('td-0 END: stopping the session closes both open streams with 4410, and a later join is 4404',
          (e1[0], e2[0], late[0]) == (4410, 4410, 4404), (e1, e2, late))
    check('td-0 DOOR STATE: the door holds no connection once every stream is closed', not door.LIVE, list(door.LIVE))
    # ---- the door's own record (one line per open, close and refusal; checkable from the container's log)
    end = time.time() + 3
    want_close = ['close session=%s stream=%s code=4410' % (sid, k) for k in ('ctl', 'uart')]
    while time.time() < end and not all(any(l.startswith(w) for l in door_log) for w in want_close):
        time.sleep(0.05)
    opens = [l for l in door_log if l.startswith('open session=%s ' % sid)]
    check('td-0 LOG: every stream that opened left an open line and a close line with its code and seconds (the last two closed 4410)',
          {l.split()[2] for l in opens} == {'stream=ctl', 'stream=uart'} and all(any(l.startswith(w) and ' after=' in l for l in door_log) for w in want_close)
          and len([l for l in door_log if l.startswith('close ')]) == len([l for l in door_log if l.startswith('open ')]), door_log[-6:])
    check('td-0 LOG: each connection turned away left a refused line with its code (4401, 4400, 4403, 4404 all seen)',
          all(any(l.startswith('refused ') and ' code=%d' % c in l for l in door_log) for c in (4401, 4400, 4403, 4404)),
          [l for l in door_log if l.startswith('refused ')])
    check('td-0 LOG: no line carries the token (nor any part of a frame)', door_log and not any(tok in l or 'token":' in l for l in door_log), '')
    src = open(os.path.join(HERE, 'twin_door.py')).read()
    check('td-0 SOURCE: the door never reads the session files and keeps no token (the worker is the only holder)',
          'SESSIONS_DIR' not in src and 'board_sessions' not in src and 'compare_digest' not in src, '')
    httpd.shutdown()


def main():
    fails = []

    def check(label, cond, extra=''):
        print('  [%s] %s%s' % ('PASS' if cond else 'FAIL', label, '' if cond else '  <- %r' % (extra,)))
        if not cond:
            fails.append(label)
    run(check)
    print('twin door selftest: %d failure(s)' % len(fails))
    return 1 if fails else 0


if __name__ == '__main__':
    raise SystemExit(main())
