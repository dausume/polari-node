"""
twin_door — td-0 (TWIN_DOOR_PLAN.md §3/§3a): the door to a worker's twin sessions, for callers on other devices.

A twin session listens on two plain TCP ports inside the worker (ports.ctl, ports.tcp). Those ports are not reachable
through an isle agent, which serves 443 only. This door is ONE port speaking WebSocket, registered as its own isle app
with its own name (twin.<worker>.isle), so the agent's ordinary HTTPS block carries both streams:

  GET /sessions/<id>/ctl    the twin's control line: text frames, one JSON line per frame (no trailing newline)
  GET /sessions/<id>/uart   the firmware's serial stream: binary frames, raw bytes both ways
  GET /health               200 when the door can reach its worker, else 503 (the release's health path)

The first frame on either stream is {"token": "..."} as text, within TOKEN_WAIT_S. The door asks the WORKER whether
the token is right (POST /sessions/<id>/check); it never stores tokens. On ctl the token line is then passed on to the
twin, whose own {"t":"ack","of":"token"} comes back as the first frame, exactly as over TCP. On uart the token frame
is consumed. A wrong token never reaches the twin (the twin hangs up for good on one).

Close codes: 4400 bad first frame, 4401 no token in time, 4403 wrong token, 4404 unknown session, 4410 session ended
or the twin closed the stream, 4409 replaced by a newer connection on the same stream, 4502 the twin's port could not
be reached, 4503 the worker could not be asked; 1003 a frame of the wrong kind; 1009 a line over the frame limit.

    TWIN_DOOR_WORKER   the worker's own HTTP address          (default http://127.0.0.1:9830)
    TWIN_DOOR_TARGET   the host the session ports are on      (default: the worker's host)
    TWIN_DOOR_PORT     where this door listens                (default 9864)

    python3 twin_door.py
"""
import asyncio
import http
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

WORKER = os.environ.get('TWIN_DOOR_WORKER', 'http://127.0.0.1:9830').rstrip('/')
TARGET = os.environ.get('TWIN_DOOR_TARGET') or urllib.parse.urlparse(WORKER).hostname
PORT = int(os.environ.get('TWIN_DOOR_PORT', '9864'))
TOKEN_WAIT_S = 5
PING_S = 20
DIAL_S = 3
MAX_FRAME = 1 << 20
PATH = re.compile(r'^/sessions/([0-9a-f]{6,32})/(ctl|uart)$')
STREAM_PORT = {'ctl': 'ctl', 'uart': 'tcp'}
LIVE = {}   # (sid, stream) -> (connection, asyncio.Event set when its twin socket is closed)
# One line per connection that opens, one when it closes, one for each that is turned away - so a proof made from another
# device can be checked from this container's log (asked for by pol, 2026-11-16: the door logged nothing per session).
# The line carries the session id, the stream, the close code and the seconds it stood. NEVER the token, never a frame.
LOG = logging.getLogger('twin_door')


def _ask(method, path, body=None, timeout=4):
    """(status, json or None) from the worker; (0, None) when it cannot be asked."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(WORKER + path, data=data, method=method, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'null')
        except ValueError:
            return e.code, None
    except (OSError, ValueError):
        return 0, None


def check(sid, token):
    """Ask the worker: is this the token of a running session? -> (close code or 0, ports or reason)."""
    status, body = _ask('POST', '/sessions/%s/check' % sid, {'token': token})
    if status == 200 and isinstance(body, dict) and isinstance(body.get('ports'), dict):
        return 0, body['ports']
    if status == 403:
        return 4403, 'the token is wrong for session %s' % sid
    if status == 404:
        return 4404, 'no session %s on this worker' % sid
    if status == 410:
        return 4410, 'session %s has ended' % sid
    return 4503, 'the worker could not be asked about session %s' % sid


async def process_request(connection, request):
    path = request.path.split('?', 1)[0]
    if path == '/health':
        status, _ = await asyncio.to_thread(_ask, 'GET', '/capability', None, 2)
        if status == 200:
            return connection.respond(http.HTTPStatus.OK, '{"ok": true, "door": "twin"}\n')
        return connection.respond(http.HTTPStatus.SERVICE_UNAVAILABLE, '{"ok": false, "error": "the door cannot reach its worker"}\n')
    if not PATH.match(path):
        return connection.respond(http.HTTPStatus.NOT_FOUND, 'this door serves /sessions/<id>/ctl and /sessions/<id>/uart\n')
    return None


def _token_of(frame):
    if not isinstance(frame, str):
        return None
    try:
        d = json.loads(frame)
    except ValueError:
        return None
    t = d.get('token') if isinstance(d, dict) else None
    return t if isinstance(t, str) and t else None


async def _up(ws, writer, stream):
    """Caller -> twin. Returns (close code, reason) when the caller breaks the contract, None when it just leaves."""
    async for frame in ws:
        if stream == 'ctl':
            if not isinstance(frame, str):
                return 1003, 'the ctl stream takes text frames only'
            line = frame.rstrip('\r\n')
            if '\n' in line or '\r' in line:
                return 1003, 'one JSON line per frame'
            writer.write(line.encode() + b'\n')
        else:
            if isinstance(frame, str):
                return 1003, 'the uart stream takes binary frames only'
            writer.write(frame)
        await writer.drain()
    return None


async def _down(reader, ws, stream):
    """Twin -> caller. Returns (close code, reason) when the twin side ends."""
    while True:
        if stream == 'ctl':
            try:
                raw = await reader.readline()
            except ValueError:
                return 1009, 'a control line over %d bytes' % MAX_FRAME
            if not raw:
                return 4410, 'the twin closed the control line'
            await ws.send(raw.rstrip(b'\r\n').decode('utf-8', 'replace'))
        else:
            raw = await reader.read(65536)
            if not raw:
                return 4410, 'the twin closed the serial stream'
            await ws.send(raw)


async def handler(ws):
    sid, stream = PATH.match(ws.request.path.split('?', 1)[0]).groups()
    try:
        first = await asyncio.wait_for(ws.recv(), TOKEN_WAIT_S)
    except asyncio.TimeoutError:
        LOG.info('refused session=%s stream=%s code=4401 (no token frame)', sid, stream)
        await ws.close(4401, 'no token frame within %d s' % TOKEN_WAIT_S)
        return
    except ConnectionClosed:
        return
    token = _token_of(first)
    if token is None:
        LOG.info('refused session=%s stream=%s code=4400 (first frame is not a token)', sid, stream)
        await ws.close(4400, 'the first frame must be {"token": "..."} as text')
        return
    code, ports = await asyncio.to_thread(check, sid, token)
    if code:
        LOG.info('refused session=%s stream=%s code=%d', sid, stream, code)
        await ws.close(code, ports)
        return
    key = (sid, stream)
    old = LIVE.get(key)
    if old is not None:   # the twin takes one client per port: the old one must be gone before the new one dials
        await old[0].close(4409, 'replaced by a newer connection')
        try:
            await asyncio.wait_for(old[1].wait(), 2)
        except asyncio.TimeoutError:
            pass
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(TARGET, int(ports[STREAM_PORT[stream]]), limit=MAX_FRAME), DIAL_S)
    except (OSError, asyncio.TimeoutError, KeyError, TypeError, ValueError):
        LOG.info('refused session=%s stream=%s code=4502 (twin port not reached)', sid, stream)
        await ws.close(4502, 'the twin port of session %s could not be reached' % sid)
        return
    gone = asyncio.Event()
    t0 = time.monotonic()
    LOG.info('open session=%s stream=%s', sid, stream)
    LIVE[key] = (ws, gone)
    tasks = []
    try:
        if stream == 'ctl':
            writer.write(first.rstrip('\r\n').replace('\n', ' ').replace('\r', ' ').encode() + b'\n')
            await writer.drain()
        tasks = [asyncio.ensure_future(_up(ws, writer, stream)), asyncio.ensure_future(_down(reader, ws, stream))]
        finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        verdict = None
        for t in finished:
            if not t.cancelled() and t.exception() is None and t.result():
                verdict = t.result()
        if verdict:
            await ws.close(*verdict)
    except (ConnectionClosed, OSError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        if LIVE.get(key, (None,))[0] is ws:
            LIVE.pop(key, None)
        gone.set()
        LOG.info('close session=%s stream=%s code=%s after=%.1fs', sid, stream, getattr(ws, 'close_code', None) or '-', time.monotonic() - t0)


async def main(host='0.0.0.0', port=PORT, ready=None):
    async with serve(handler, host, port, process_request=process_request, ping_interval=PING_S, ping_timeout=PING_S,
                     max_size=MAX_FRAME, open_timeout=10) as server:
        if ready is not None:
            ready(server)
        await server.serve_forever()


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s twin-door %(message)s', datefmt='%Y-%m-%dT%H:%M:%SZ')
    logging.Formatter.converter = time.gmtime
    logging.getLogger('websockets').setLevel(logging.WARNING)   # its INFO is one line per /health probe and per handshake: it would bury the session lines
    LOG.info('listening on port %d, worker %s', PORT, WORKER)
    asyncio.run(main())
