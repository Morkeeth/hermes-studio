"""Bring-your-own video model: quote, approval, cap, and the full chat loop against a fake provider."""
import io
import json
import os
import subprocess
import sys
import tempfile
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
            self.assertIn('Reply YES', q['message'])
            with self.assertRaises(PermissionError):
                gen_api.generate(q, 'maybe', '/tmp/never')

    def test_cap_blocks(self):
        with Env({'big': {'provider': 'fal', 'model': 'fal-ai/big', 'cost_usd': 25.0}}):
            q = gen_api.quote('big', 'x', monthly_cap_usd=20.0)
            self.assertTrue(any('cap' in b for b in q['blockers']))
            with self.assertRaises(PermissionError):
                gen_api.generate(q, 'YES', '/tmp/never')

    def test_unknown_cost_is_said_out_loud(self):
        with Env({'mystery': {'provider': 'replicate', 'model': 'owner/name'}}):
            q = gen_api.quote('mystery', 'x')
            self.assertIn('UNKNOWN', q['message'])

    def test_unknown_model_points_to_models_json(self):
        with Env({}):
            with self.assertRaises(ValueError) as e:
                gen_api.quote('nope', 'x')
            self.assertIn('models.json', str(e.exception))


class FullLoop(unittest.TestCase):
    def test_finished_short_quote_yes_generated_version(self):
        with Env({'demo': {'provider': 'fal', 'model': 'fal-ai/demo', 'cost_usd': 0.4}}), tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fin = finish.finish(clip(tmp / 'raw.mp4'), tmp / 'v1', title='Hook', words=[{'text': 'hi', 'start': 0.2, 'end': 0.5}], intro_seconds=0.6)
            shot_bytes = clip(tmp / 'shot.mp4', seconds=4, size='720x1280').read_bytes()
            q = gen_api.quote('demo', 'animated blue intro')
            with patch.object(gen_api, '_run_fal', return_value={'video': {'url': 'https://cdn.example/out.mp4'}}), \
                 patch('connectors.gen_api.urllib.request.urlopen', return_value=io.BytesIO(shot_bytes)):
                g = gen_api.generate(q, 'YES', tmp / 'gen')
            self.assertTrue(Path(g['video']).is_file())
            self.assertAlmostEqual(gen_api.spent_this_month(), 0.4)
            r = finish.insert_shot(fin['output'], g['video'], tmp / 'v2', seconds=3.0)
            self.assertAlmostEqual(r['seconds'], 3.0 + fin['seconds'], delta=0.3)
            with self.assertRaises(FileExistsError):
                finish.insert_shot(fin['output'], g['video'], tmp / 'v2')


if __name__ == '__main__':
    unittest.main()
