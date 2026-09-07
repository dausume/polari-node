"""LXMF store-and-forward messaging for the pol-reticulum sidecar
(ret-7, Dustin's greenlight 2026-08-23). FOUNDATIONAL and
own-stack: built on the licence-pinned lxmf==0.6.3 / rns==0.9.4
(the last MIT releases — RETICULUM_LICENCE_GATE.md) and nothing
else; no third-party gateway project referenced.

Design:
  - The sidecar's persistent RNS identity gains an LXMF delivery
    destination (register_delivery_identity) — THE ISLE is the
    addressable party (§5r doctrine: identity, not address;
    humans act through the backend's KC+provenance surface).
  - Inbound messages pass the POLICY GATE before storage:
    * whitelist: empty = open门 (rate-limited); non-empty = only
      listed source hashes are stored
    * rate limit: per-sender token window (default 30 msgs/5 min)
    Refused messages are LEDGERED with the reason — silence hides
    abuse, refusal records expose it.
  - Accepted messages persist as jsonl in the sidecar volume
    (store-and-forward across restarts), capped oldest-first.
  - send(): destination identity must already be KNOWN
    (RNS.Identity.recall after an announce was heard) — an
    unknown hash is an honest refusal naming the recovery, never
    a silent queue to nowhere.

RNS/LXMF modules are INJECTED (rns_module/lxmf_module) so the
whole policy/store/send path is testable without a radio stack;
the sidecar wires the real ones.
"""

import json
import os
import threading
import time

STORE_CAP = 500
REFUSAL_CAP = 200


class MessagingPolicy:
    def __init__(self, whitelist=None, max_per_window=30,
                 window_s=300):
        self.whitelist = list(whitelist or [])
        self.max_per_window = int(max_per_window)
        self.window_s = int(window_s)
        self._buckets = {}

    def as_dict(self):
        return {'whitelist': list(self.whitelist),
                'maxPerWindow': self.max_per_window,
                'windowSeconds': self.window_s,
                'mode': 'whitelist' if self.whitelist
                        else 'open-rate-limited'}

    def admit(self, source_hex, now=None):
        """(admitted, reason). Whitelist first, then the window."""
        if self.whitelist and source_hex not in self.whitelist:
            return False, (f'sender {source_hex[:16]}… not on the '
                           'whitelist')
        now = now if now is not None else time.time()
        bucket = [t for t in self._buckets.get(source_hex, [])
                  if now - t < self.window_s]
        if len(bucket) >= self.max_per_window:
            return False, (f'rate limit: {self.max_per_window} '
                           f'msgs/{self.window_s}s exceeded by '
                           f'{source_hex[:16]}…')
        bucket.append(now)
        self._buckets[source_hex] = bucket
        return True, ''


class LxmfMessaging:
    """The sidecar's messaging unit. start() builds the router;
    everything else is data + policy."""

    def __init__(self, identity, storage_dir, rns_module=None,
                 lxmf_module=None, display_name='polari-isle'):
        self.identity = identity
        self.storage_dir = storage_dir
        self.rns = rns_module
        self.lxmf = lxmf_module
        self.display_name = display_name
        self.policy = MessagingPolicy()
        self.router = None
        self.destination = None
        self.messages = []
        self.refusals = []
        self.outbound = []
        self._lock = threading.Lock()
        self._store_path = os.path.join(storage_dir,
                                        'lxmf-messages.jsonl')
        self._load()

    # ---- lifecycle -------------------------------------------------
    def start(self):
        if self.rns is None or self.lxmf is None:
            return {'ok': False,
                    'refusal': 'rns/lxmf modules not provided — '
                               'the licence-pinned stack is absent'}
        os.makedirs(self.storage_dir, exist_ok=True)
        self.router = self.lxmf.LXMRouter(
            storagepath=self.storage_dir)
        self.destination = self.router.register_delivery_identity(
            self.identity, display_name=self.display_name)
        self.router.register_delivery_callback(self._on_delivery)
        try:
            self.router.announce(self.destination.hash)
        except Exception:
            pass  # announce cadence is advisory; facts show state
        return {'ok': True,
                'destinationHash': self._hex(
                    self.destination.hash)}

    def _hex(self, data):
        try:
            return self.rns.hexrep(data, delimit=False)
        except Exception:
            return data.hex() if isinstance(data, (bytes, bytearray)) \
                else str(data)

    # ---- inbound ---------------------------------------------------
    def _on_delivery(self, lxm):
        source = self._hex(getattr(lxm, 'source_hash', b''))
        admitted, reason = self.policy.admit(source)
        if not admitted:
            with self._lock:
                self.refusals.append({
                    'source': source, 'reason': reason,
                    'at': time.time()})
                del self.refusals[:-REFUSAL_CAP]
            return
        content = getattr(lxm, 'content', b'')
        if isinstance(content, (bytes, bytearray)):
            content = content.decode('utf-8', 'replace')
        title = getattr(lxm, 'title', b'')
        if isinstance(title, (bytes, bytearray)):
            title = title.decode('utf-8', 'replace')
        record = {
            'hash': self._hex(getattr(lxm, 'hash', b'')),
            'source': source,
            'title': title, 'content': content,
            'signatureValidated': bool(
                getattr(lxm, 'signature_validated', False)),
            'receivedAt': time.time(),
        }
        with self._lock:
            self.messages.append(record)
            del self.messages[:-STORE_CAP]
            self._persist(record)

    # ---- outbound --------------------------------------------------
    def send(self, dest_hash_hex, content, title=''):
        if self.router is None:
            return {'ok': False,
                    'refusal': 'messaging not started'}
        try:
            dest_bytes = bytes.fromhex(dest_hash_hex)
        except Exception:
            return {'ok': False,
                    'error': f'"{dest_hash_hex}" is not a hex '
                             'destination hash'}
        recalled = self.rns.Identity.recall(dest_bytes)
        if recalled is None:
            return {'ok': False,
                    'refusal': 'destination identity unknown on '
                               'this node — it has not been heard '
                               'announcing yet; wait for (or '
                               'request) its announce, then retry'}
        dest = self.rns.Destination(
            recalled, self.rns.Destination.OUT,
            self.rns.Destination.SINGLE, 'lxmf', 'delivery')
        lxm = self.lxmf.LXMessage(
            dest, self.destination, content, title,
            desired_method=self.lxmf.LXMessage.OPPORTUNISTIC)
        self.router.handle_outbound(lxm)
        record = {'to': dest_hash_hex, 'title': title,
                  'bytes': len(content.encode('utf-8'))
                  if isinstance(content, str) else len(content),
                  'queuedAt': time.time(),
                  'lxmHash': self._hex(getattr(lxm, 'hash', b''))}
        with self._lock:
            self.outbound.append(record)
            del self.outbound[:-STORE_CAP]
        return {'ok': True, **record}

    # ---- persistence ----------------------------------------------
    def _persist(self, record):
        try:
            with open(self._store_path, 'a') as fh:
                fh.write(json.dumps(record) + '\n')
        except Exception:
            pass

    def _load(self):
        try:
            with open(self._store_path) as fh:
                for line in fh:
                    try:
                        self.messages.append(json.loads(line))
                    except Exception:
                        continue
            del self.messages[:-STORE_CAP]
        except FileNotFoundError:
            pass

    # ---- surface ---------------------------------------------------
    def facts(self):
        with self._lock:
            return {
                'destinationHash': self._hex(
                    self.destination.hash)
                if self.destination is not None else '',
                'started': self.router is not None,
                'storedMessages': len(self.messages),
                'outboundQueued': len(self.outbound),
                'refusals': len(self.refusals),
                'policy': self.policy.as_dict(),
            }

    def list_messages(self, limit=50):
        with self._lock:
            return list(self.messages[-int(limit):])

    def list_refusals(self, limit=50):
        with self._lock:
            return list(self.refusals[-int(limit):])

    def set_policy(self, whitelist=None, max_per_window=None,
                   window_s=None):
        if whitelist is not None:
            self.policy.whitelist = list(whitelist)
        if max_per_window is not None:
            self.policy.max_per_window = int(max_per_window)
        if window_s is not None:
            self.policy.window_s = int(window_s)
        return self.policy.as_dict()
