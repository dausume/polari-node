"""pol-reticulum sidecar (ret-2 + ret-1d listener + lighthouse v1):
the ONLY process in the suite that imports RNS (the licence boundary
— see the Dockerfile header and RETICULUM_LICENCE_GATE.md).

Deliberately thin:
  1. Runs the Reticulum stack (config from /var/reticulum, seeded
     from the committed template on first start; persistent instance
     identity in the volume).
  2. /status on :4285 — stack version, THE LICENCE PINS, identity,
     per-interface facts, and peersHeard (ret-1d: every announce
     heard, so the backend's /peers can show potential peers;
     HEARING IS NOT ADMITTING — rows exist only after a human
     adjudicates).
  3. Lighthouse v1 (plan §5n): broadcasts a mesh app's state as
     PLAIN packets over whatever interfaces are attached — ALL
     bearers, not just HAM (row 21) — at a cadence the BACKEND sets
     (the adaptive algorithm is application logic and lives there;
     this process obeys and reports). Collects consumer returns and
     exposes them per Reticulum identity for census + cadence.
     ⚠ v1 returns are SELF-LABELLED (identity hash inside the
     envelope, not link-authenticated) — stated, and the
     link-authenticated return path is the named follow-up.
     STATE DELTAS (§5f semantics, first walk of the inter-arch
     efficiency story): a POST that isn't a forced keyframe
     broadcasts only the CHANGES vs the prior payload
     (kind 'delta', parentVersion carried); KEYFRAMES ARE
     MANDATORY — every Nth broadcast (keyframe_every_n, default 5)
     or any keyframe:true POST sends the FULL state, because on a
     one-way path a keyframe is the ONLY recovery a late joiner or
     a gap has. Byte counts per kind are MEASURED and exposed in
     the facts (deltaBytes vs keyframeBytes — the saving is a
     number, not a claim). Deltas are JSON in v1; gRPC/protobuf
     bodies are ret-5's turn.

Nothing in this process writes Polari rows — inbound data goes to
the backend's /api/reticulum/inbound seam where it becomes a
PROPOSAL (ret-8), and a lighthouse with no state to shine is silent
(row 19: no state posted = no broadcast loop = a dark radio).
"""

import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_DIR = os.environ.get('RNS_CONFIG_DIR', '/var/reticulum')
STATUS_PORT = int(os.environ.get('RETICULUM_STATUS_PORT', '4285'))
STARTED = time.time()

os.makedirs(CONFIG_DIR, exist_ok=True)
if not os.path.exists(os.path.join(CONFIG_DIR, 'config')):
    shutil.copy('/opt/config.template',
                os.path.join(CONFIG_DIR, 'config'))
    print('[sidecar] seeded config from template', flush=True)

import RNS  # noqa: E402  (after config seeding, by design)

PINS = {'rns': '0.9.4', 'lxmf': '0.6.3',
        'gate': 'RETICULUM_LICENCE_GATE.md'}

reticulum = RNS.Reticulum(configdir=CONFIG_DIR)

_identity_path = os.path.join(CONFIG_DIR, 'polari_identity')
if os.path.exists(_identity_path):
    identity = RNS.Identity.from_file(_identity_path)
    if identity is None:
        raise SystemExit('[sidecar] identity file exists but failed to '
                         'load — refusing to silently mint a new one '
                         '(wipe the volume to re-key deliberately)')
else:
    identity = RNS.Identity()
    identity.to_file(_identity_path)
    print('[sidecar] generated instance identity', flush=True)

print(f'[sidecar] up; identity '
      f'{RNS.hexrep(identity.hash, delimit=False)}', flush=True)

# ---- ret-1d: hear announces; hearing is not admitting ----------------
PEERS = {}
_PEERS_LOCK = threading.Lock()


class AnnounceListener:
    aspect_filter = None  # hear EVERYTHING; adjudication is human

    def received_announce(self, destination_hash, announced_identity,
                          app_data):
        dest = RNS.hexrep(destination_hash, delimit=False)
        ident = RNS.hexrep(announced_identity.hash, delimit=False) \
            if announced_identity else ''
        now_ms = int(time.time() * 1000)
        with _PEERS_LOCK:
            entry = PEERS.setdefault(dest, {
                'destHash': dest, 'identityHash': ident,
                'firstHeardMs': now_ms, 'count': 0,
                'appData': ''})
            entry['count'] += 1
            entry['lastHeardMs'] = now_ms
            if app_data:
                try:
                    entry['appData'] = app_data[:64].decode(
                        'utf-8', 'ignore')
                except Exception:
                    pass
        print(f'[sidecar] heard announce {dest} (count '
              f'{entry["count"]})', flush=True)


RNS.Transport.register_announce_handler(AnnounceListener())

# ---- lighthouse v1 (plan §5n): shine state, collect returns ----------
LIGHTHOUSES = {}
_LH_LOCK = threading.Lock()

#: RNS packets cap at ~500 B; keyframes MUST fit in one (they are the
#: recovery mechanism — a state whose recovery cannot be broadcast is
#: not broadcastable). Envelope budget leaves headroom for framing.
#: Larger state is ret-7's road (LXMF/Resource fragmentation).
KEYFRAME_BYTE_BUDGET = 450


class Lighthouse:
    """One relay: broadcasts posted state as PLAIN packets on aspects
    ('polari','lighthouse',<name>) at the cadence the backend set;
    listens for returns on ('polari','lighthouse-returns',<name>)."""

    def __init__(self, name):
        self.name = name
        self.state = None          # {'payload','version'} — CURRENT
        self.pending_delta = None  # {'delta','version','parentVersion'}
        self.force_keyframe = True  # first broadcast is always full
        self.keyframe_every_n = 5
        self.cadence_s = 60.0
        self.last_broadcast_ms = 0
        self.broadcasts = 0
        self.keyframes_sent = 0
        self.deltas_sent = 0
        self.keyframe_bytes = 0
        self.delta_bytes = 0
        self.consumers = {}        # identityHash -> return facts
        self.out_dest = RNS.Destination(
            None, RNS.Destination.OUT, RNS.Destination.PLAIN,
            'polari', 'lighthouse', name)
        self.returns_dest = RNS.Destination(
            None, RNS.Destination.IN, RNS.Destination.PLAIN,
            'polari', 'lighthouse-returns', name)
        self.returns_dest.set_packet_callback(self._return_received)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _return_received(self, message, packet):
        try:
            body = json.loads(message.decode('utf-8'))
        except Exception:
            return
        ident = body.get('identityHash', '')
        if not ident:
            return
        now_ms = int(time.time() * 1000)
        entry = self.consumers.setdefault(ident, {
            'firstSeenMs': now_ms, 'returns': 0,
            'lastReturnMs': 0, 'returnIntervalsS': []})
        if entry['lastReturnMs']:
            interval = (now_ms - entry['lastReturnMs']) / 1000.0
            entry['returnIntervalsS'] = (
                entry['returnIntervalsS'] + [round(interval, 2)])[-5:]
        entry['returns'] += 1
        entry['lastReturnMs'] = now_ms
        entry['lastVersion'] = body.get('receivedVersion')

    def keyframe_size(self, payload, version):
        """What the keyframe envelope for this payload would weigh —
        checked BEFORE adoption, because a keyframe that cannot fly
        makes the whole relay unrecoverable."""
        return len(json.dumps({'kind': 'state', 'relay': self.name,
                               'version': version, 'keyframe': True,
                               'tsMs': 0, 'payload': payload}
                              ).encode('utf-8'))

    def post_state(self, payload, version, keyframe=False):
        """Adopt new state; decide what the next broadcasts carry.
        Deltas only between dict payloads with a prior to diff
        against — anything else forces a keyframe (you cannot delta
        what you cannot diff). Returns 'keyframe' or 'delta'."""
        prior = self.state
        deltable = (not keyframe and prior is not None
                    and isinstance(prior.get('payload'), dict)
                    and isinstance(payload, dict))
        if deltable:
            changed = {k: v for k, v in payload.items()
                       if prior['payload'].get(k, object()) != v}
            removed = sorted(k for k in prior['payload']
                             if k not in payload)
            self.pending_delta = {
                'delta': {'changed': changed, 'removed': removed},
                'version': version,
                'parentVersion': prior['version'],
            }
            mode = 'delta'
        else:
            self.pending_delta = None
            self.force_keyframe = True
            mode = 'keyframe'
        self.state = {'payload': payload, 'version': version}
        return mode

    def _envelope(self):
        """What THIS broadcast carries: a mandatory keyframe (forced,
        every-Nth, or nothing to delta) or the pending delta."""
        nth_due = self.keyframe_every_n and \
            self.broadcasts % self.keyframe_every_n == 0
        if self.force_keyframe or nth_due or self.pending_delta is None:
            kind = 'keyframe'
            body = {'kind': 'state', 'relay': self.name,
                    'version': self.state['version'],
                    'keyframe': True,
                    'tsMs': int(time.time() * 1000),
                    'payload': self.state['payload']}
        else:
            kind = 'delta'
            body = {'kind': 'delta', 'relay': self.name,
                    'version': self.pending_delta['version'],
                    'parentVersion':
                        self.pending_delta['parentVersion'],
                    'tsMs': int(time.time() * 1000),
                    'delta': self.pending_delta['delta']}
        return kind, json.dumps(body).encode('utf-8')

    def _loop(self):
        while not self._stop.is_set():
            if self.state is not None:
                kind, envelope = self._envelope()
                try:
                    RNS.Packet(self.out_dest, envelope).send()
                    self.broadcasts += 1
                    self.last_broadcast_ms = int(time.time() * 1000)
                    if kind == 'keyframe':
                        self.keyframes_sent += 1
                        self.keyframe_bytes += len(envelope)
                        self.force_keyframe = False
                    else:
                        self.deltas_sent += 1
                        self.delta_bytes += len(envelope)
                except Exception as e:
                    print(f'[lighthouse:{self.name}] send failed: {e}',
                          flush=True)
            self._stop.wait(self.cadence_s)

    def facts(self):
        avg_kf = (self.keyframe_bytes / self.keyframes_sent) \
            if self.keyframes_sent else None
        avg_delta = (self.delta_bytes / self.deltas_sent) \
            if self.deltas_sent else None
        return {
            'name': self.name,
            'version': self.state['version'] if self.state else None,
            'cadenceSeconds': self.cadence_s,
            'keyframeEveryN': self.keyframe_every_n,
            'broadcasts': self.broadcasts,
            'keyframesSent': self.keyframes_sent,
            'deltasSent': self.deltas_sent,
            'keyframeBytes': self.keyframe_bytes,
            'deltaBytes': self.delta_bytes,
            'avgKeyframeBytes': round(avg_kf, 1) if avg_kf else None,
            'avgDeltaBytes': round(avg_delta, 1) if avg_delta
            else None,
            'deltaNote': 'byte counts are MEASURED from envelopes '
                         'actually sent; keyframes are mandatory '
                         '(every Nth or forced) — the only recovery '
                         'on a one-way path',
            'lastBroadcastMs': self.last_broadcast_ms,
            'consumers': self.consumers,
            'consumerCount': len(self.consumers),
            'returnsNote': 'v1 returns are self-labelled by identity '
                           'hash — link-authenticated returns are the '
                           'named follow-up',
        }

    def stop(self):
        self._stop.set()


def interface_facts():
    facts = []
    for iface in RNS.Transport.interfaces:
        facts.append({
            'name': str(getattr(iface, 'name', iface)),
            'online': bool(getattr(iface, 'online', False)),
            'bitrate': getattr(iface, 'bitrate', None),
            'mode': getattr(iface, 'mode', None),
        })
    return facts


class StatusHandler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip('/')
        if path in ('', '/status'):
            with _PEERS_LOCK:
                peers = list(PEERS.values())
            return self._json(200, {
                'ok': True,
                'service': 'pol-reticulum',
                'rnsVersion': getattr(RNS, '__version__', 'unknown'),
                'stackPins': PINS,
                'identityHash': RNS.hexrep(identity.hash,
                                           delimit=False),
                'interfaces': interface_facts(),
                'peersHeard': peers,
                'lighthouses': sorted(LIGHTHOUSES),
                'uptimeSeconds': int(time.time() - STARTED),
            })
        if path.startswith('/lighthouse/'):
            name = path.split('/')[2]
            lh = LIGHTHOUSES.get(name)
            if lh is None:
                return self._json(404, {
                    'ok': False,
                    'error': 'no lighthouse %r — a lighthouse exists '
                             'only after state is POSTed to it (no '
                             'state = no broadcast = a dark radio, '
                             'DECIDED row 19)' % name})
            return self._json(200, dict(lh.facts(), ok=True))
        return self._json(404, {'ok': False, 'error':
                                'only /status and /lighthouse/* '
                                'live here'})

    def do_POST(self):
        path = self.path.rstrip('/')
        length = int(self.headers.get('Content-Length', 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except Exception:
            return self._json(400, {'ok': False,
                                    'error': 'unparseable JSON body'})
        parts = path.split('/')
        if len(parts) == 4 and parts[1] == 'lighthouse':
            name, verb = parts[2], parts[3]
            with _LH_LOCK:
                lh = LIGHTHOUSES.get(name)
                if verb == 'state':
                    if 'payload' not in body or 'version' not in body:
                        return self._json(400, {
                            'ok': False, 'error': 'state needs '
                            'payload + version'})
                    if lh is None:
                        lh = LIGHTHOUSES[name] = Lighthouse(name)
                    kf_bytes = lh.keyframe_size(body['payload'],
                                                body['version'])
                    if kf_bytes > KEYFRAME_BYTE_BUDGET:
                        if not LIGHTHOUSES[name].broadcasts \
                                and LIGHTHOUSES[name].state is None:
                            lh.stop()
                            del LIGHTHOUSES[name]
                        return self._json(413, {
                            'ok': False,
                            'error': 'keyframe envelope would be %d B '
                                     'against a %d B budget (RNS '
                                     'packet MTU ~500 B) — a state '
                                     'whose RECOVERY cannot be '
                                     'broadcast is not broadcastable. '
                                     'Shrink the payload, or wait for '
                                     'ret-7 (LXMF/Resource '
                                     'fragmentation) for larger state.'
                                     % (kf_bytes,
                                        KEYFRAME_BYTE_BUDGET),
                            'keyframeBytes': kf_bytes,
                            'budgetBytes': KEYFRAME_BYTE_BUDGET})
                    if 'keyframeEveryN' in body:
                        lh.keyframe_every_n = int(body['keyframeEveryN'])
                    mode = lh.post_state(body['payload'],
                                         body['version'],
                                         bool(body.get('keyframe',
                                                       False)))
                    if 'cadenceSeconds' in body:
                        lh.cadence_s = float(body['cadenceSeconds'])
                    return self._json(200, {'ok': True,
                                            'lighthouse': name,
                                            'version': body['version'],
                                            'mode': mode})
                if lh is None:
                    return self._json(404, {'ok': False,
                                            'error': 'no lighthouse '
                                                     '%r' % name})
                if verb == 'cadence':
                    if not body.get('cadenceSeconds'):
                        return self._json(400, {
                            'ok': False,
                            'error': 'cadenceSeconds required'})
                    lh.cadence_s = float(body['cadenceSeconds'])
                    return self._json(200, {'ok': True,
                                            'cadenceSeconds':
                                                lh.cadence_s})
                if verb == 'stop':
                    lh.stop()
                    del LIGHTHOUSES[name]
                    return self._json(200, {
                        'ok': True, 'stopped': name,
                        'note': 'the radio goes dark for this relay '
                                '(row 19)'})
        return self._json(404, {'ok': False,
                                'error': 'unknown lighthouse verb'})

    def log_message(self, fmt, *args):  # quiet: status polls are noise
        pass


server = ThreadingHTTPServer(('0.0.0.0', STATUS_PORT), StatusHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f'[sidecar] /status on :{STATUS_PORT}', flush=True)

while True:
    time.sleep(60)
