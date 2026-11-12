"""The take tree: a reply to any older video starts a new version from it, and `tree` draws every version.

Gateway records and workstation results are real files in a temp folder. Telegram is faked.
"""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bridge_rpc
import channel
import connector_worker
import take_tree
from test_hero_loop import make_clip

CHAT = 42


def gateway(tmp):
    gw = Path(tmp) / 'gateway'; (gw / 'jobs').mkdir(parents=True)
    (gw / 'config.json').write_text(json.dumps({'inbound_roots': [str(tmp)], 'delivery_chat_id': CHAT, 'allowed_chat_ids': [CHAT]}))
    return gw


def job(gw, jid, action, result, parent=None, style=None, mid=None, created=0.0, query=''):
    obj = {'id': jid, 'action': action, 'state': 'completed', 'parent': parent, 'style': style, 'query': query,
           'created_at': created, 'result': result}
    if mid:
        obj['delivery'] = {'state': 'sent', 'message_id': mid, 'chat_id': CHAT}
    (gw / 'jobs' / (jid + '.json')).write_text(json.dumps(obj))
    return obj


ALT = {'state': 'alternatives_ready', 'options': [{'option': 'A', 'label': 'Clean short', 'style': 'clean-short'},
                                                   {'option': 'B', 'label': 'Brain rot', 'style': 'brain-rot'},
                                                   {'option': 'C', 'label': 'Hermes dither', 'style': 'hermes-dither'}]}


def fixture_project(gw):
    """Alternatives, V1 from C, two versions made from V1 (a branch), V3 from the second branch, plus a quote."""
    a, v1, v2, v2b, q, v3 = ('a' * 32, '1' * 32, '2' * 32, 'b' * 32, 'c' * 32, '3' * 32)
    job(gw, a, 'alternatives', ALT, created=1)
    job(gw, v1, 'finish', {'state': 'finished', 'style': 'hermes-dither', 'revision': 0}, parent=a, style='C', mid=11, created=2)
    job(gw, v2, 'revise', {'state': 'revised', 'changed': ['style the current look', 'opening tightened by 0.5 s'], 'skipped': [], 'revision': 1},
        parent=v1, mid=12, created=3)
    job(gw, v2b, 'revise', {'state': 'revised', 'changed': ['style the current look', 'new title'], 'skipped': ['quieter music: one mixed track'],
                            'revision': 1}, parent=v1, mid=13, created=4)
    job(gw, q, 'generate', {'state': 'awaiting_approval'}, parent=v2b, created=5)
    job(gw, v3, 'approve', {'state': 'generated', 'revision': 2}, parent=q, mid=15, created=6, query='YES ABC123')
    return a, v1, v2, v2b, q, v3


class Graph(unittest.TestCase):
    def test_labels_name_a_branch_instead_of_a_second_version_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            a, v1, v2, v2b, q, v3 = fixture_project(gw)
            nodes = take_tree.graph(take_tree.load(gw))
            self.assertEqual({k: nodes[k]['label'] for k in (v1, v2, v2b, v3)}, {v1: 'V1', v2: 'V2', v2b: 'V2b', v3: 'V3'})
            self.assertEqual(nodes[v1]['parent'], a + ':C')  # made from the offered option C
            self.assertEqual(nodes[v3]['parent'], v2b)  # the quote is not a video: the shot hangs off V2b
            self.assertEqual(take_tree.label(gw, v2b), ('V2b', 'V1'))
            self.assertNotIn(q, nodes)

    def test_two_projects_each_start_at_v1_and_tree_shows_one_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            fixture_project(gw)
            other = 'd' * 32
            job(gw, other, 'finish', {'state': 'finished', 'style': 'brain-rot'}, mid=20, created=9)
            self.assertEqual(take_tree.label(gw, other), ('V1', None))
            newest = take_tree.snapshot(gw)
            self.assertEqual([n['id'] for n in newest], [other])
            older = take_tree.snapshot(gw, '2' * 32)
            self.assertEqual(sum(n['kind'] == 'version' for n in older), 4)
            self.assertEqual(sum(n['kind'] == 'option' for n in older), 3)

    def test_snapshot_never_carries_a_query_or_an_approval_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            fixture_project(gw)
            text = json.dumps(take_tree.snapshot(gw))
            self.assertNotIn('ABC123', text)
            self.assertNotIn('query', text)


class TreeRequest(unittest.TestCase):
    def test_tree_is_an_action_and_stores_the_project_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            fixture_project(gw)
            req = channel.request({'action': 'tree', 'reply_to_message_id': 12, 'request_key': 'tg:42:30:tree'}, gw)
            self.assertEqual((req['action'], req['state']), ('tree', 'queued'))
            self.assertEqual({n['label'] for n in req['tree']}, {'A', 'B', 'C', 'V1', 'V2', 'V2b', 'V3'})

    def test_tree_without_any_video_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            with self.assertRaisesRegex(ValueError, 'No delivered video yet'):
                channel.request({'action': 'tree', 'request_key': 'tg:42:31:tree'}, gw)

    def test_gateway_copies_are_identical(self):
        for name in ('channel.py', 'take_tree.py'):
            self.assertEqual((HERE / name).read_bytes(), (HERE / 'gateway' / name).read_bytes(), name)


class ReplyToAnOlderVideo(unittest.TestCase):
    """V1 is finished. The creator revises it (V2), then replies to V1 again. The new version starts from V1."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); t = Path(self.tmp.name)
        self.gw, self.ws = gateway(t), t / 'ws'; self.ws.mkdir()
        from connectors import finish
        raw = make_clip(t / 'raw.mp4')
        fin = finish.finish(raw, t / 'fin', title='t', words=[{'text': 'hi', 'start': 0.2, 'end': 0.5}], style='hermes-dither')
        self.v1 = '1' * 32
        res = {'state': 'finished', 'style': 'hermes-dither', 'source': str(raw), 'recipe': fin['recipe'], 'preview': fin['output'],
               'preview_sha256': connector_worker._sha(fin['output']), 'revision': 0, 'project_id': self.v1}
        (self.ws / self.v1).mkdir()
        connector_worker.atomic(self.ws / self.v1 / 'result.json', res)
        job(self.gw, self.v1, 'finish', res, mid=11, created=1)
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_send(self, path, sha, conf, receipt, caption):
        mid = 100 + len(self.sent)
        self.sent.append({'mid': mid, 'caption': caption})
        return {'state': 'sent', 'sha256': sha, 'message_id': mid, 'chat_id': CHAT}

    def reply(self, mid, key, edit):
        req = channel.request({'action': 'revise', 'reply_to_message_id': mid, 'request_key': key, 'edit': edit}, self.gw)
        state, result = connector_worker.process({'workspace': str(self.ws)}, req, None)
        connector_worker.atomic(self.ws / req['id'] / 'result.json', result)
        bridge_rpc.rpc(self.gw, {'op': 'finish', 'id': req['id'], 'state': state, 'result': result})
        with patch('telegram_delivery.send_preview', side_effect=self.fake_send):
            bridge_rpc.rpc(self.gw, {'op': 'deliver', 'id': req['id'], 'base64': base64.b64encode(Path(result['preview']).read_bytes()).decode()})
        return req, result

    def test_branch_starts_from_the_replied_video_and_is_named_as_a_branch(self):
        a, ra = self.reply(11, 'tg:42:2:revise', {'trim_start': 0.5})
        b, rb = self.reply(11, 'tg:42:3:revise', {'title': 'Other way'})  # a reply to V1 again, not to V2
        self.assertEqual((a['parent'], b['parent']), (self.v1, self.v1))
        self.assertEqual(json.loads(Path(ra['recipe']).read_text()).get('start_at'), 0.5)
        self.assertFalse(json.loads(Path(rb['recipe']).read_text()).get('start_at'))  # V2's trim is not carried into the branch
        self.assertTrue(self.sent[0]['caption'].startswith('V2, made from V1.'), self.sent[0]['caption'])
        self.assertTrue(self.sent[1]['caption'].startswith('V2b, made from V1.'), self.sent[1]['caption'])
        self.assertIn('Previous version kept', self.sent[1]['caption'])
        # a reply to the branch continues the branch
        c, rc = self.reply(self.sent[1]['mid'], 'tg:42:4:revise', {'punchier': True})
        self.assertEqual(c['parent'], b['id'])
        self.assertTrue(self.sent[2]['caption'].startswith('V3, made from V2b.'), self.sent[2]['caption'])

    def test_tree_image_is_drawn_and_sent_as_one_photo(self):
        self.reply(11, 'tg:42:2:revise', {'trim_start': 0.5})
        self.reply(11, 'tg:42:3:revise', {'title': 'Other way'})
        req = channel.request({'action': 'tree', 'request_key': 'tg:42:5:tree'}, self.gw)
        state, result = connector_worker.process({'workspace': str(self.ws)}, req, None)
        self.assertEqual((state, result['state'], result['versions']), ('completed', 'tree_ready', 3))
        from PIL import Image
        with Image.open(result['image']) as im:
            self.assertGreaterEqual(im.size, (900, 300))
        bridge_rpc.rpc(self.gw, {'op': 'finish', 'id': req['id'], 'state': state, 'result': result})
        photos = []

        def fake_photo(path, sha, conf, receipt, caption):
            photos.append(caption)
            return {'state': 'sent', 'sha256': sha, 'message_id': 300, 'chat_id': CHAT}
        with patch('telegram_delivery.send_photo', side_effect=fake_photo):
            bridge_rpc.rpc(self.gw, {'op': 'deliver_photo', 'id': req['id'], 'base64': base64.b64encode(Path(result['image']).read_bytes()).decode()})
        self.assertIn('3 versions', photos[0])
        stored = json.loads((self.gw / 'jobs' / (req['id'] + '.json')).read_text())
        self.assertEqual(stored['photo_delivery']['message_id'], 300)
        with self.assertRaisesRegex(ValueError, 'No unique delivered project'):
            channel.status(None, self.gw, reply_to_message_id=300)  # a reply to the image is not a reply to a video

    def test_send_photo_uses_the_photo_endpoint(self):
        import telegram_delivery
        png = Path(self.tmp.name) / 'tree.png'
        from PIL import Image
        Image.new('RGB', (10, 10)).save(png)
        seen = {}

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps({'ok': True, 'result': {'message_id': 5, 'chat': {'id': CHAT}}}).encode()

        def fake_open(req, timeout):
            seen['url'], seen['body'] = req.full_url, req.data
            return Reply()
        with patch.object(telegram_delivery, 'secret_value', return_value='TOKEN'), patch('urllib.request.urlopen', fake_open):
            out = telegram_delivery.send_photo(png, connector_worker._sha(png), {'delivery_chat_id': CHAT, 'allowed_chat_ids': [CHAT]},
                                               Path(self.tmp.name) / 'r.json', 'tree')
        self.assertTrue(seen['url'].endswith('/sendPhoto'))
        self.assertIn(b'name="photo"; filename="studio-tree.png"', seen['body'])
        self.assertEqual(out['message_id'], 5)


def delivered(gw, jid, action, result, parent=None, created=0.0, state='completed', **extra):
    """A job whose preview is a small real file, ready for the bridge 'deliver' op."""
    import hashlib
    clip = gw / (jid + '.bin'); clip.write_bytes(jid.encode())
    result = dict(result, preview=str(clip), preview_sha256=hashlib.sha256(clip.read_bytes()).hexdigest())
    obj = {'id': jid, 'action': action, 'state': state, 'parent': parent, 'created_at': created, 'result': result, **extra}
    (gw / 'jobs' / (jid + '.json')).write_text(json.dumps(obj))
    return clip


def deliver(gw, jid, clip, mid):
    seen = []

    def fake(path, sha, conf, receipt, caption):
        seen.append(caption)
        return {'state': 'sent', 'sha256': sha, 'message_id': mid, 'chat_id': CHAT}
    with patch('telegram_delivery.send_preview', side_effect=fake):
        bridge_rpc.rpc(gw, {'op': 'deliver', 'id': jid, 'base64': base64.b64encode(clip.read_bytes()).decode()})
    return seen[0]


class Captions(unittest.TestCase):
    def test_finished_and_generated_captions_name_the_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            v1, q, v2 = '1' * 32, 'c' * 32, '2' * 32
            clip = delivered(gw, v1, 'finish', {'state': 'finished', 'style': 'brain-rot'}, created=1)
            cap = deliver(gw, v1, clip, 11)
            self.assertTrue(cap.startswith('V1. Finished: brain-rot.'), cap)
            self.assertIn('Reply to any older video to start from it instead.', cap)
            job(gw, q, 'generate', {'state': 'awaiting_approval'}, parent=v1, created=2)
            clip = delivered(gw, v2, 'approve', {'state': 'generated', 'model': 'm'}, parent=q, created=3)
            self.assertTrue(deliver(gw, v2, clip, 12).startswith('V2: New version with a generated opening'))

    def test_a_name_once_sent_never_changes_when_jobs_finish_out_of_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw = gateway(tmp)
            v1, x, y = '1' * 32, 'e' * 32, 'f' * 32
            deliver(gw, v1, delivered(gw, v1, 'finish', {'state': 'finished', 'style': 'brain-rot'}, created=1), 11)
            rev = {'state': 'revised', 'changed': ['style the current look'], 'skipped': []}
            clip_x = delivered(gw, x, 'revise', rev, parent=v1, created=2, state='queued')  # asked first, still rendering
            clip_y = delivered(gw, y, 'revise', rev, parent=v1, created=3)  # asked second, finished first
            self.assertTrue(deliver(gw, y, clip_y, 13).startswith('V2, made from V1.'))
            obj = json.loads((gw / 'jobs' / (x + '.json')).read_text()); obj['state'] = 'completed'
            (gw / 'jobs' / (x + '.json')).write_text(json.dumps(obj))
            self.assertTrue(deliver(gw, x, clip_x, 14).startswith('V2b, made from V1.'))
            nodes = take_tree.graph(take_tree.load(gw))
            self.assertEqual((nodes[y]['label'], nodes[x]['label']), ('V2', 'V2b'))  # the tree shows what the captions said


class WorkerDeliversTheTree(unittest.TestCase):
    def run_worker(self, gw, ws, **patches):
        cfg = Path(ws).parent / 'worker.json'
        cfg.write_text(json.dumps({'workspace': str(ws)}))
        with patch.object(connector_worker, 'remote', lambda c, payload: bridge_rpc.rpc(gw, payload)), \
                patch.object(sys, 'argv', ['worker', '--config', str(cfg), '--deliver-ready']), \
                patch('builtins.print'):
            connector_worker.main()

    def test_tree_job_is_sent_as_a_photo_and_a_failure_is_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, ws = gateway(tmp), Path(tmp) / 'ws'; ws.mkdir()
            fixture_project(gw)
            ok = channel.request({'action': 'tree', 'request_key': 'tg:42:40:tree'}, gw)
            bad = channel.request({'action': 'tree', 'request_key': 'tg:42:41:tree'}, gw)
            (ws / bad['id']).mkdir()  # its folder already exists, so this tree step fails
            photos, texts = [], []
            with patch('telegram_delivery.send_photo', side_effect=lambda *a: photos.append(a[4]) or {'state': 'sent', 'message_id': 400, 'chat_id': CHAT}), \
                    patch('telegram_delivery.send_text', side_effect=lambda t, c: texts.append(t) or {'state': 'sent', 'message_id': 401}):
                self.run_worker(gw, ws)
            self.assertEqual(len(photos), 1)
            self.assertIn('4 versions', photos[0])
            self.assertEqual(json.loads((gw / 'jobs' / (ok['id'] + '.json')).read_text())['photo_delivery']['message_id'], 400)
            self.assertTrue(any(t.startswith('That did not work') for t in texts), texts)


if __name__ == '__main__':
    unittest.main()
