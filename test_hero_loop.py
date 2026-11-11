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
                    if job['action'] in ('alternatives', 'finish', 'revise') and state == 'completed':
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
            # 3b. one reply, several changes: keep C, A's captions, tighter and punchier opening, quieter music
            channel.request({'action': 'revise', 'reply_to_message_id': sent[4]['mid'], 'request_key': 'tg:42:3b:revise',
                             'edit': {'style': 'C', 'captions_from': 'A', 'trim_start': 0.5, 'punchier': True, 'quieter_music': True}}, gw)
            job, state, result = worker_pass()
            self.assertEqual((state, result['state'], result['revision']), ('completed', 'revised', 2))
            self.assertTrue(any('captions from clean-short' in c for c in result['changed']))
            self.assertTrue(any('tightened' in c for c in result['changed']))
            self.assertTrue(any('quieter music' in k for k in result['skipped']))  # mixed track: said plainly, not faked
            self.assertIn('Could not: quieter music', sent[-1]['caption'])
            self.assertIn('Previous version kept', sent[-1]['caption'])
            # 3c. "make it look like this": a reference clip becomes the look
            ref = inbound / 'reference.mp4'
            subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=orange:size=320x240:rate=30:duration=2',
                            '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=30:duration=2', '-filter_complex', '[0][1]blend=all_mode=multiply',
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(ref)], check=True)
            channel.request({'action': 'revise', 'reply_to_message_id': sent[-1]['mid'], 'source': str(ref), 'request_key': 'tg:42:3c:look',
                             'edit': {'look_from_source': True}}, gw)
            job, state, result = worker_pass()
            self.assertEqual((state, result['state']), ('completed', 'revised'))
            self.assertIn('look taken from your reference clip', result['changed'])
            # 4. a letter that was never offered fails honestly
            channel.request({'action': 'finish', 'reply_to_message_id': sent[4]['mid'], 'style': 'D', 'request_key': 'tg:42:4:finish'}, gw)
            job, state, result = worker_pass()
            self.assertEqual(state, 'failed')
            # every state the creator can see comes with a plain sentence
            import channel as ch
            self.assertIn('Reply to one with its letter', ch.status(None, gw, reply_to_message_id=sent[0]['mid'])['explain'])
            self.assertIn('no new video exists', ch.explain({'state': 'awaiting_render_gate', 'result': {'state': 'awaiting_render_gate'}})['explain'])


class Conversation(unittest.TestCase):
    def test_wrapup_names_title_options_and_honest_limits(self):
        text = connector_worker.wrapup({'title': 'welcome to Hermes Studio', 'options': [{'option': 'A', 'label': 'Clean short', 'style': 'clean-short'}, {'option': 'B', 'label': 'Brain rot', 'style': 'brain-rot'}]})
        self.assertIn('welcome to Hermes Studio', text)
        self.assertIn('B: Brain rot', text)
        self.assertIn('cannot see whether it played', text)
        fast = connector_worker.wrapup({'title': 't', 'options': [{'option': 'B', 'label': 'Brain rot', 'style': 'brain-rot'}],
                                        'editor_note': {'pick': 'brain-rot', 'why': 'You talk fast.'}})
        self.assertIn('My pick: B. You talk fast.', fast)

    def test_notify_goes_through_the_bridge_to_the_owner_chat_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = Path(tmp); (gw / 'jobs').mkdir()
            (gw / 'config.json').write_text(json.dumps({'inbound_roots': [tmp], 'delivery_chat_id': 42, 'allowed_chat_ids': [42]}))
            job = channel.request({'action': 'library', 'query': 'x', 'request_key': 'k1'}, gw)
            with patch('telegram_delivery.send_text', return_value={'state': 'sent', 'message_id': 9}) as st:
                r = bridge_rpc.rpc(gw, {'op': 'notify', 'id': job['id'], 'text': 'ready'})
            self.assertEqual(r['state'], 'sent')
            self.assertEqual(st.call_args[0][0], 'ready')


class MusicBed(unittest.TestCase):
    def test_quieter_music_is_real_when_a_bed_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); ws = tmp / 'ws'; ws.mkdir()
            raw = make_clip(tmp / 'raw.mp4')
            bed = tmp / 'bed.m4a'
            subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=220:duration=8', '-c:a', 'aac', str(bed)], check=True)
            from connectors import finish
            fin = finish.finish(raw, tmp / 'fin', title='t', words=[{'text': 'hi', 'start': 0.2, 'end': 0.5}], style='hermes-dither', music=str(bed), music_db=-20)
            (ws / ('p' * 32)).mkdir()
            connector_worker.atomic(ws / ('p' * 32) / 'result.json', {'state': 'finished', 'style': 'hermes-dither', 'source': str(raw),
                                                                       'recipe': fin['recipe'], 'preview': fin['output'], 'revision': 1})
            job = {'id': 'q' * 32, 'action': 'revise', 'parent': 'p' * 32, 'edit': {'quieter_music': True}}
            state, result = connector_worker.process({'workspace': str(ws)}, job, None)
            self.assertTrue(any('6 dB quieter' in c for c in result['changed']))
            self.assertEqual(result['skipped'], [])
            recipe = json.loads(Path(result['recipe']).read_text())
            self.assertEqual(recipe['music_db'], -26)


if __name__ == '__main__':
    unittest.main()
