"""Bring-your-own video model: quote, approval, cap, and the full chat loop against a fake provider."""
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
from connectors import gen_api, finish


def clip(path, seconds=3, size='640x360'):
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', str(path)], check=True)
    return path


class Env:
    def __init__(self, models, key=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / 'models.json').write_text(json.dumps(models))
        self.p = patch.object(gen_api, 'CONFIG', self.dir)
        self.e = patch.dict(os.environ, {'FAL_KEY': 'test-key'} if key else {}, clear=False)

    def __enter__(self):
        self.p.start(); self.e.start()
        return self

    def __exit__(self, *a):
        self.p.stop(); self.e.stop(); self.tmp.cleanup()


class Quote(unittest.TestCase):
    def test_no_spend_without_yes(self):
        with Env({'demo': {'provider': 'fal', 'model': 'fal-ai/demo', 'cost_usd': 0.4}}):
            q = gen_api.quote('demo', 'a slow blue push-in')
            self.assertIn('reply exactly: YES ' + q['approval_code'], q['message'])
            with self.assertRaises(PermissionError):
                gen_api.generate(q, 'maybe', '/tmp/never')

    def test_cap_blocks(self):
        with Env({'big': {'provider': 'fal', 'model': 'fal-ai/big', 'cost_usd': 25.0}}):
            q = gen_api.quote('big', 'x', monthly_cap_usd=20.0)
            self.assertTrue(any('cap' in b for b in q['blockers']))
            with self.assertRaises(PermissionError):
                gen_api.generate(q, 'YES ' + q['approval_code'], '/tmp/never')

    def test_unknown_cost_is_said_out_loud(self):
        with Env({'mystery': {'provider': 'replicate', 'model': 'owner/name'}}):
            q = gen_api.quote('mystery', 'x')
            self.assertIn('UNKNOWN', q['message'])

    def test_unknown_model_points_to_models_json(self):
        with Env({}):
            with self.assertRaises(ValueError) as e:
                gen_api.quote('nope', 'x')
            self.assertIn('models.json', str(e.exception))


class HardCap(unittest.TestCase):
    def fake(self):
        return patch.object(gen_api, '_run_fal', return_value={'video': {'url': 'https://cdn.example/x.mp4'}}), \
               patch('connectors.gen_api.urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(b'video-bytes'))

    def test_unknown_price_is_blocked_under_a_hard_cap(self):
        with Env({'mystery': {'provider': 'fal', 'model': 'fal-ai/m'}}):
            q = gen_api.quote('mystery', 'x')
            self.assertTrue(any('price unknown' in b for b in q['blockers']))
            with self.assertRaises(PermissionError):
                gen_api.generate(q, 'YES ' + q['approval_code'], tempfile.mkdtemp())

    def test_a_local_maximum_is_not_treated_as_a_price(self):
        # a max_usd only lives on this Mac; the provider could charge more, so it must not unblock a call
        with Env({'capped': {'provider': 'fal', 'model': 'fal-ai/c', 'max_usd': 3.0}}):
            q = gen_api.quote('capped', 'x')
            self.assertTrue(any('price unknown' in b for b in q['blockers']))

    def test_cap_is_rechecked_when_money_moves(self):
        with Env({'twelve': {'provider': 'fal', 'model': 'fal-ai/t', 'cost_usd': 12.0}}):
            q1, q2 = gen_api.quote('twelve', 'one'), gen_api.quote('twelve', 'two')
            self.assertEqual((q1['blockers'], q2['blockers']), ([], []))  # each fits on its own at quote time
            a, b = self.fake()
            with a as run, b:
                gen_api.generate(q1, 'YES ' + q1['approval_code'], tempfile.mkdtemp())
                with self.assertRaises(PermissionError) as e:
                    gen_api.generate(q2, 'YES ' + q2['approval_code'], tempfile.mkdtemp())
                self.assertEqual(run.call_count, 1)
            self.assertIn('at spend time', str(e.exception))
            self.assertAlmostEqual(gen_api.spent_this_month(), 12.0)

    def test_two_simultaneous_approvals_cannot_both_spend(self):
        import threading
        with Env({'twelve': {'provider': 'fal', 'model': 'fal-ai/t', 'cost_usd': 12.0}}):
            qs = [gen_api.quote('twelve', f'p{i}') for i in range(2)]
            results = []
            a, b = self.fake()
            real = gen_api.spent_this_month
            def slow():  # widen the race: read the ledger, then pause, like a slow disk
                v = real(); time.sleep(0.3); return v
            with a as run, b, patch.object(gen_api, 'spent_this_month', side_effect=slow):
                def go(q):
                    try:
                        gen_api.generate(q, 'YES ' + q['approval_code'], tempfile.mkdtemp()); results.append('spent')
                    except PermissionError:
                        results.append('refused')
                ts = [threading.Thread(target=go, args=(q,)) for q in qs]
                [t.start() for t in ts]; [t.join() for t in ts]
                self.assertEqual(sorted(results), ['refused', 'spent'])
                self.assertEqual(run.call_count, 1)


class FullLoop(unittest.TestCase):
    def test_finished_short_quote_yes_generated_version(self):
        with Env({'demo': {'provider': 'fal', 'model': 'fal-ai/demo', 'cost_usd': 0.4}}), tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fin = finish.finish(clip(tmp / 'raw.mp4'), tmp / 'v1', title='Hook', words=[{'text': 'hi', 'start': 0.2, 'end': 0.5}], intro_seconds=0.6)
            shot_bytes = clip(tmp / 'shot.mp4', seconds=4, size='720x1280').read_bytes()
            q = gen_api.quote('demo', 'animated blue intro')
            with patch.object(gen_api, '_run_fal', return_value={'video': {'url': 'https://cdn.example/out.mp4'}}), \
                 patch('connectors.gen_api.urllib.request.urlopen', return_value=io.BytesIO(shot_bytes)):
                g = gen_api.generate(q, 'YES ' + q['approval_code'], tmp / 'gen')
            self.assertTrue(Path(g['video']).is_file())
            self.assertAlmostEqual(gen_api.spent_this_month(), 0.4)
            r = finish.insert_shot(fin['output'], g['video'], tmp / 'v2', seconds=3.0)
            self.assertAlmostEqual(r['seconds'], 3.0 + fin['seconds'], delta=0.3)
            with self.assertRaises(FileExistsError):
                finish.insert_shot(fin['output'], g['video'], tmp / 'v2')


if __name__ == '__main__':
    unittest.main()
