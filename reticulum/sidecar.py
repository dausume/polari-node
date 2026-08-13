"""pol-reticulum sidecar (ret-2): the ONLY process in the suite that
imports RNS (the licence boundary — see the Dockerfile header and
RETICULUM_LICENCE_GATE.md).

Does two things, deliberately small:
  1. Runs the Reticulum stack (config from /var/reticulum, seeded from
     the committed template on first start; a persistent instance
     identity generated into the volume).
  2. Serves an honest /status API on :4285 for the backend's
     rns_remote ladder: stack version, THE LICENCE PINS, identity
     hash, per-interface online/bitrate facts.

The gateway (ret-3: synthetic-IP mapping, name registry, admission)
and the replication engine (ret-7) will grow HERE, not in the
backend image. Nothing in this process writes Polari rows — inbound
data goes to the backend's /api/reticulum/inbound seam, where it
becomes a PROPOSAL (ret-8).
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
    def do_GET(self):
        if self.path.rstrip('/') not in ('', '/status'):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"ok": false, "error": "only /status '
                             b'lives here"}')
            return
        body = json.dumps({
            'ok': True,
            'service': 'pol-reticulum',
            'rnsVersion': getattr(RNS, '__version__', 'unknown'),
            'stackPins': PINS,
            'identityHash': RNS.hexrep(identity.hash, delimit=False),
            'interfaces': interface_facts(),
            'uptimeSeconds': int(time.time() - STARTED),
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet: status polls are noise
        pass


server = ThreadingHTTPServer(('0.0.0.0', STATUS_PORT), StatusHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f'[sidecar] /status on :{STATUS_PORT}', flush=True)

while True:
    time.sleep(60)
