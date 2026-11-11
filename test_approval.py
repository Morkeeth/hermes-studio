"""Adversarial tests: money moves only on the creator's own YES + one-time code, once, before expiry,
and the code never reaches the gateway or the agent."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import channel
import connector_worker
from connectors import gen_api, finish


def clip(path, seconds=3, size='640x360'):
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', str(path)], check=True)
    return path


class Approval(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.cfgdir = t / 'cfg'; self.cfgdir.mkdir()
        (self.cfgdir / 'models.json').write_text(json.dumps({'demo': {'provider': 'fal', 'model': 'fal-ai/demo', 'cost_usd': 0.4}}))
        self.p1 = patch.object(gen_api, 'CONFIG', self.cfgdir); self.p1.start()
        self.p2 = patch.dict(os.environ, {'FAL_KEY': 'k'}); self.p2.start()
        self.ws = t / 'ws'; self.ws.mkdir()
        fin = finish.finish(clip(t / 'raw.mp4'), t / 'fin', title='Hook', words=[{'text': 'hi', 'start': 0.2, 'end': 0.5}], intro_seconds=0.6)
        (self.ws / ('f' * 32)).mkdir()
        connector_worker.atomic(self.ws / ('f' * 32) / 'result.json', {'state': 'finished', 'preview': fin['output'], 'source': str(t / 'raw.mp4')})
        self.cfg = {'workspace': str(self.ws)}
        self.shot = clip(t / 'shot.mp4', seconds=4, size='720x1280').read_bytes()

    def tearDown(self):
        self.p1.stop(); self.p2.stop(); self.tmp.cleanup()

    def quote_job(self):
        job = {'id': 'a' * 32, 'action': 'generate', 'parent': 'f' * 32, 'title': 'demo', 'query': 'blue intro'}
        state, result = connector_worker.process(self.cfg, job, None)
        msg = result.pop('quote_message')  # exactly what the real worker does before anything leaves
        connector_worker.atomic(self.ws / job['id'] / 'result.json', result)
        result['quote_message'] = msg
        return result

    def approve(self, text, ident):
        job = {'id': ident, 'action': 'approve', 'parent': 'a' * 32, 'query': text}
        with patch.object(gen_api, '_run_fal', return_value={'video': {'url': 'https://cdn.example/x.mp4'}}) as run, \
             patch('connectors.gen_api.urllib.request.urlopen', return_value=io.BytesIO(self.shot)):
            try:
                return connector_worker.process(self.cfg, job, None), run.call_count
            except Exception as e:
                return ('failed', str(e)), run.call_count

    def test_code_never_reaches_gateway_result(self):
        r = self.quote_job()
        self.assertNotIn('approval_code', json.dumps(r))
        self.assertIn('YES ', r['quote_message'])  # only the chat message carries it
        self.assertNotIn('approval_code', r['quote'])

    def test_agent_plain_yes_is_refused_at_the_gateway(self):
        gw = Path(self.tmp.name) / 'gw'; (gw / 'jobs').mkdir(parents=True)
        (gw / 'config.json').write_text(json.dumps({'inbound_roots': [self.tmp.name], 'delivery_chat_id': 1, 'allowed_chat_ids': [1]}))
        quote = {'id': '9' * 32, 'state': 'completed', 'action': 'generate', 'result': {'state': 'awaiting_approval'}}
        (gw / 'jobs' / ('9' * 32 + '.json')).write_text(json.dumps(quote))
        for text in ('YES', 'yes please', 'YES ZZZZZZ', ''):
            with self.assertRaises(ValueError) as e:
                channel.request({'action': 'approve', 'parent': '9' * 32, 'query': text, 'request_key': 'k-' + text}, gw)
            self.assertIn('Never supply it yourself', str(e.exception))

    def test_wrong_code_spends_nothing(self):
        self.quote_job()
        (state, why), calls = self.approve('YES 000000', 'b' * 32)
        self.assertEqual((state, calls, gen_api.spent_this_month()), ('failed', 0, 0.0))
        self.assertIn('does not match', why)

    def test_right_code_spends_once_then_never_again(self):
        r = self.quote_job()
        code = r['quote_message'].split('YES ')[1].split()[0]
        (state, res), calls = self.approve('YES ' + code, 'c' * 32)
        self.assertEqual((state, calls), ('completed', 1))
        self.assertEqual(res['state'], 'generated')
        (state2, why), calls2 = self.approve('YES ' + code, 'd' * 32)
        self.assertEqual((state2, calls2), ('failed', 0))
        self.assertIn('already spent', why)
        self.assertAlmostEqual(gen_api.spent_this_month(), 0.4)

    def test_expired_quote_spends_nothing(self):
        r = self.quote_job()
        code = r['quote_message'].split('YES ')[1].split()[0]
        sp = self.ws / ('a' * 32) / 'quote-secret.json'
        q = json.loads(sp.read_text()); q['expires_at'] = time.time() - 1; sp.write_text(json.dumps(q))
        (state, why), calls = self.approve('YES ' + code, 'e' * 32)
        self.assertEqual((state, calls), ('failed', 0))
        self.assertIn('expired', why)


    def test_code_from_another_quote_spends_nothing(self):
        r1 = self.quote_job()
        code1 = r1['quote_message'].split('YES ')[1].split()[0]
        # a second quote for the same short: its code differs, and quote 1's code must not open it
        job2 = {'id': '2' * 32, 'action': 'generate', 'parent': 'f' * 32, 'title': 'demo', 'query': 'other idea'}
        state, r2 = connector_worker.process(self.cfg, job2, None); r2.pop('quote_message')
        connector_worker.atomic(self.ws / job2['id'] / 'result.json', r2)
        job = {'id': '3' * 32, 'action': 'approve', 'parent': '2' * 32, 'query': 'YES ' + code1}
        with patch.object(gen_api, '_run_fal') as run:
            try:
                connector_worker.process(self.cfg, job, None); outcome = 'spent'
            except PermissionError as e:
                outcome = str(e)
        self.assertIn('does not match', outcome)
        self.assertEqual(run.call_count, 0)

    def test_reply_from_outside_the_owner_chat_cannot_approve(self):
        gw = Path(self.tmp.name) / 'gw2'; (gw / 'jobs').mkdir(parents=True)
        (gw / 'config.json').write_text(json.dumps({'inbound_roots': [self.tmp.name], 'delivery_chat_id': 1, 'allowed_chat_ids': [1]}))
        quote = {'id': '8' * 32, 'state': 'completed', 'action': 'generate', 'result': {'state': 'awaiting_approval'},
                 'deliveries': [{'state': 'sent', 'message_id': 55, 'chat_id': 999}]}  # sent to a different chat
        (gw / 'jobs' / ('8' * 32 + '.json')).write_text(json.dumps(quote))
        with self.assertRaises(ValueError) as e:
            channel.request({'action': 'approve', 'reply_to_message_id': 55, 'query': 'YES ABCDEF', 'request_key': 'k-x'}, gw)
        self.assertIn('No unique delivered project', str(e.exception))

    def test_two_simultaneous_approvals_of_one_quote_spend_once(self):
        import threading
        r = self.quote_job()
        code = r['quote_message'].split('YES ')[1].split()[0]
        outcomes = []
        real_loads = json.loads

        def slow_loads(text, *a, **k):  # widen the window between reading the quote and marking it used
            v = real_loads(text, *a, **k)
            if isinstance(v, dict) and 'approval_code' in v:
                time.sleep(0.3)
            return v
        with patch.object(gen_api, '_run_fal', return_value={'video': {'url': 'https://cdn.example/x.mp4'}}) as run, \
             patch('connectors.gen_api.urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(self.shot)), \
             patch('connector_worker.json.loads', side_effect=slow_loads):
            def go(i):
                try:
                    connector_worker.process(self.cfg, {'id': f'{i}' * 32, 'action': 'approve', 'parent': 'a' * 32, 'query': 'YES ' + code}, None)
                    outcomes.append('spent')
                except Exception as e:
                    outcomes.append('refused')
            ts = [threading.Thread(target=go, args=(i,)) for i in (5, 6)]
            [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(outcomes), ['refused', 'spent'])
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
