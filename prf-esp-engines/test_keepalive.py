#!/usr/bin/env python3
"""The worker's /run answer: as before when the run is short, a stream of spaces and then the JSON when it is long.
A made-up engine (sh); no ESP tool, no network.  Run: python3 test_keepalive.py"""
import json
import os
import unittest

os.environ['WORKER_KEEPALIVE_S'] = '0.2'
import falcon.testing

import esp_engines_service as svc

svc.ENGINES['sh'] = 'sh'


class KeepAlive(unittest.TestCase):
    def setUp(self):
        self.client = falcon.testing.TestClient(svc.app)

    def run_sh(self, script, **more):
        return self.client.simulate_post('/run', json=dict(engine='sh', args=['-c', script], **more))

    def test_a_short_run_answers_as_before(self):
        r = self.run_sh('echo hi > out.txt; echo said')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.content.startswith(b' '))
        self.assertEqual((r.json['ok'], r.json['stdout'], r.json['files']), (True, 'said\n', {'out.txt': 'hi\n'}))
        self.assertIsNone(r.headers.get('X-Accel-Buffering'))

    def test_a_long_run_sends_spaces_and_then_the_same_json(self):
        r = self.run_sh('sleep 0.7; echo late')
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r.content.decode(), r'^ {2,4}\{')
        self.assertEqual(r.headers.get('X-Accel-Buffering'), 'no')
        d = json.loads(r.content)
        self.assertEqual((d['ok'], d['returncode'], d['stdout'], d['stdout_chars']), (True, 0, 'late\n', 5))

    def test_a_refusal_met_after_the_stream_began_is_said_in_the_body(self):
        r = self.run_sh('sleep 5', timeout=0.5)
        self.assertEqual(r.status_code, 200)
        d = json.loads(r.content)
        self.assertEqual((d['ok'], d['status']), (False, 504))
        self.assertIn('exceeded', d['error'])

    def test_a_refusal_before_the_run_keeps_its_status(self):
        self.assertEqual(self.client.simulate_post('/run', json=dict(engine='nothing')).status_code, 400)
        self.assertEqual(self.client.simulate_post('/run', json=dict(engine='esptool', args=['write_flash'])).status_code, 403)

    def test_zero_turns_the_stream_off(self):
        was, svc.KEEPALIVE_S = svc.KEEPALIVE_S, 0
        try:
            r = self.run_sh('sleep 0.5; echo late')
            self.assertFalse(r.content.startswith(b' ')); self.assertEqual(r.json['stdout'], 'late\n')
            self.assertEqual(self.run_sh('sleep 5', timeout=0.3).status_code, 504)
        finally:
            svc.KEEPALIVE_S = was


if __name__ == '__main__':
    unittest.main()
