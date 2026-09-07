"""
Selftest for the sidecar's LXMF messaging unit (ret-7).

Run from polari-rf-node/reticulum/:
  python3 selftest_lxmf_messaging.py

Fully injected fake rns/lxmf modules — the policy gate, storage,
persistence, and send refusals all run without a radio stack. The
real-stack leg is the sidecar container's own bring-up.
"""

import json
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lxmf_messaging import (  # noqa: E402
    LxmfMessaging, MessagingPolicy, REFUSAL_CAP, STORE_CAP,
)

_results = []


def check(label, cond, extra=''):
    _results.append((label, bool(cond)))
    print(f'{"PASS" if cond else "FAIL"}: {label}'
          + (f' — {extra}' if extra and not cond else ''))


class FakeRouter:
    def __init__(self, storagepath=None):
        self.storagepath = storagepath
        self.outbound = []
        self.callback = None
        self.announced = []

    def register_delivery_identity(self, identity, display_name=''):
        return types.SimpleNamespace(hash=b'\xaa' * 16)

    def register_delivery_callback(self, cb):
        self.callback = cb

    def handle_outbound(self, lxm):
        self.outbound.append(lxm)

    def announce(self, dest_hash):
        self.announced.append(dest_hash)


def make_fakes(known_identities):
    fake_lxmf = types.SimpleNamespace(
        LXMRouter=FakeRouter,
        LXMessage=types.SimpleNamespace)

    class FakeLXMessage:
        DIRECT = 'direct'
        OPPORTUNISTIC = 'opportunistic'

        def __init__(self, destination, source, content, title,
                     desired_method=None):
            self.destination = destination
            self.source = source
            self.content = content
            self.title = title
            self.desired_method = desired_method
            self.hash = b'\xbb' * 16
    fake_lxmf.LXMessage = FakeLXMessage

    class FakeDestination:
        OUT = 'out'
        SINGLE = 'single'

        def __init__(self, identity, direction, kind, app, aspect):
            self.identity = identity

    fake_rns = types.SimpleNamespace(
        hexrep=lambda data, delimit=False: data.hex(),
        Identity=types.SimpleNamespace(
            recall=lambda h: known_identities.get(h)),
        Destination=FakeDestination)
    return fake_rns, fake_lxmf


def deliver(unit, source_hex, content=b'hi', title=b't',
            validated=True):
    unit._on_delivery(types.SimpleNamespace(
        source_hash=bytes.fromhex(source_hex),
        content=content, title=title,
        signature_validated=validated, hash=b'\xcc' * 16))


def main():
    known = {b'\x11' * 16: object()}
    fake_rns, fake_lxmf = make_fakes(known)

    with tempfile.TemporaryDirectory() as store:
        unit = LxmfMessaging(object(), store, rns_module=fake_rns,
                             lxmf_module=fake_lxmf)
        report = unit.start()
        check('start: router built, delivery destination '
              'registered + announced',
              report['ok'] and report['destinationHash'] == 'aa' * 16
              and unit.router.announced == [b'\xaa' * 16])

        sender_a = '01' * 16
        deliver(unit, sender_a,
                content=b'hello isle', title=b'greeting')
        check('inbound: open-door policy admits, message stored '
              'with decoded content + signature flag',
              len(unit.messages) == 1
              and unit.messages[0]['content'] == 'hello isle'
              and unit.messages[0]['title'] == 'greeting'
              and unit.messages[0]['signatureValidated'] is True
              and unit.messages[0]['source'] == sender_a)

        # whitelist gate
        unit.set_policy(whitelist=['02' * 16])
        deliver(unit, sender_a)
        check('policy: whitelist refuses the unlisted sender BY '
              'NAME and ledgers it (no silent drop)',
              len(unit.messages) == 1 and len(unit.refusals) == 1
              and 'whitelist' in unit.refusals[0]['reason'])
        unit.set_policy(whitelist=[])

        # rate limit
        unit.set_policy(max_per_window=3, window_s=300)
        for _ in range(5):
            deliver(unit, sender_a)
        check('policy: rate limit admits the window then refuses '
              'the overflow with the count in the reason',
              len(unit.messages) == 3  # 1 earlier consumed a
              # token too: 3-cap window admits 2 more
              and any('rate limit' in r['reason']
                      for r in unit.refusals))

        # persistence across restart
        unit2 = LxmfMessaging(object(), store,
                              rns_module=fake_rns,
                              lxmf_module=fake_lxmf)
        check('store-and-forward: messages persist across a '
              'restart (jsonl reload)',
              len(unit2.messages) == len(unit.messages))

        # send: unknown identity refuses honestly
        unit.set_policy(max_per_window=30)
        report = unit.send('99' * 16, 'to nowhere')
        check('send: unknown destination identity REFUSES with '
              'the recovery named (announce first) — never a '
              'silent queue to nowhere',
              not report['ok'] and 'announce' in report['refusal'])
        report = unit.send('zz', 'bad hex')
        check('send: non-hex destination is a plain error',
              not report['ok'] and 'hex' in report['error'])

        # send: known identity queues opportunistically
        report = unit.send(('11' * 16), 'ping', title='check')
        check('send: known identity -> LXMessage queued '
              'OPPORTUNISTIC via the router, outbound ledgered',
              report['ok'] and len(unit.router.outbound) == 1
              and unit.router.outbound[0].desired_method
              == 'opportunistic'
              and unit.outbound[0]['to'] == '11' * 16)

        facts = unit.facts()
        check('facts: destination, counts, and the policy are '
              'surfaced (the /status payload)',
              facts['destinationHash'] == 'aa' * 16
              and facts['storedMessages'] == 3
              and facts['outboundQueued'] == 1
              and facts['refusals'] >= 3
              and facts['policy']['mode'] == 'open-rate-limited')

        # caps
        for i in range(STORE_CAP + 40):
            deliver(unit, sender_a)
            unit.policy._buckets.clear()  # bypass rate for cap test
        check('caps: message store and refusal ledger both stay '
              'bounded',
              len(unit.messages) <= STORE_CAP
              and len(unit.refusals) <= REFUSAL_CAP)

    passed = sum(1 for _, ok in _results if ok)
    print(f'\n{passed}/{len(_results)} checks passed')
    return 0 if passed == len(_results) else 1


if __name__ == '__main__':
    sys.exit(main())
