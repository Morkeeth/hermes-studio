"""The hero moment, end to end with a fake Telegram: raw clip in, three style options out,
reply with a letter, finished short out, reply with another letter, re-cut out."""
import base64
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import channel
import bridge_rpc
import connector_worker


def make_clip(path, seconds=3):
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=640x360:rate=30:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=330:duration={seconds}', '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', str(path)], check=True)
    return path


class HeroLoop(unittest.TestCase):
    def test_raw_clip_to_alternatives_to_finished_to_recut(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            gw, inbound, ws = tmp / 'gateway', tmp / 'inbound', tmp / 'workstation'
            for p in (gw, inbound, ws):
                p.mkdir()
            (gw / 'config.json').write_text(json.dumps({'inbound_roots': [str(inbound)], 'delivery_chat_id': 42, 'allowed_chat_ids': [42]}))
            raw = make_clip(inbound / 'raw.mp4')
            cfg = {'workspace': str(ws), 'library_roots': [str(tmp)], 'whisper_model': None}
            sent = []

            def fake_send(path, sha, conf, receipt, caption):
                mid = 100 + len(sent)
                sent.append({'mid': mid, 'caption': caption, 'path': str(path)})
                return {'state': 'sent', 'sha256': sha, 'message_id': mid, 'chat_id': 42}

            words = [{'text': 'raw', 'start': 0.2, 'end': 0.6}, {'text': 'footage', 'start': 0.6, 'end': 1.2}]

            def worker_pass():
                for job in bridge_rpc.rpc(gw, {'op': 'pending'}):
                    fetch = lambda i: bridge_rpc.rpc(gw, {'op': 'attachment', 'id': i})
                    try:
                        with patch('connectors.finish.transcribe_words', return_value=words):
                            state, result = connector_worker.process(cfg, job, fetch)
                    except Exception as e:  # the real worker records failures the same way
                        state, result = 'failed', {'error': str(e)}
                    if job['action'] in ('alternatives', 'finish') and state == 'completed':
                        connector_worker.atomic(Path(ws) / job['id'] / 'result.json', result)
                    bridge_rpc.rpc(gw, {'op': 'finish', 'id': job['id'], 'state': state, 'result': result})
                    with patch('telegram_delivery.send_preview', side_effect=fake_send):
                        if result.get('state') == 'alternatives_ready':
                            for o in result['options']:
                                bridge_rpc.rpc(gw, {'op': 'deliver_option', 'id': job['id'], 'option': o['option'],
                                                    'base64': base64.b64encode(Path(o['preview']).read_bytes()).decode()})
                        elif result.get('preview'):
                            bridge_rpc.rpc(gw, {'op': 'deliver', 'id': job['id'], 'base64': base64.b64encode(Path(result['preview']).read_bytes()).decode()})
                    return job, state, result

            # 1. raw clip in chat
            channel.request({'action': 'alternatives', 'source': str(raw), 'title': 'Raw to finished', 'request_key': 'tg:42:1:alt'}, gw)
            job, state, result = worker_pass()
            self.assertEqual(state, 'completed')
            self.assertEqual([o['option'] for o in result['options']], ['A', 'B', 'C'])
            self.assertEqual(len(sent), 3)
            self.assertTrue(sent[1]['caption'].startswith('B: Brain rot'))
            # 2. reply "B" to the brain rot preview
            channel.request({'action': 'finish', 'reply_to_message_id': sent[1]['mid'], 'style': 'B', 'request_key': 'tg:42:2:finish'}, gw)
            job, state, result = worker_pass()
            self.assertEqual((state, result['state'], result['style']), ('completed', 'finished', 'brain-rot'))
            self.assertTrue(Path(result['preview']).is_file() and Path(result['captions']).is_file())
            self.assertEqual(len(sent), 4)
            self.assertIn('Finished: brain-rot', sent[3]['caption'])
            # 3. reply "C" to the finished video: a re-cut in Hermes dither, from the same source
            channel.request({'action': 'finish', 'reply_to_message_id': sent[3]['mid'], 'style': 'C', 'request_key': 'tg:42:3:finish'}, gw)
            job, state, result = worker_pass()
            self.assertEqual((result['style'], result['revision']), ('hermes-dither', 1))
            self.assertEqual(len(sent), 5)
            # 4. a letter that was never offered fails honestly
            channel.request({'action': 'finish', 'reply_to_message_id': sent[4]['mid'], 'style': 'D', 'request_key': 'tg:42:4:finish'}, gw)
            job, state, result = worker_pass()
            self.assertEqual(state, 'failed')


if __name__ == '__main__':
    unittest.main()
