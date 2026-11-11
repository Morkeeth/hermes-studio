import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from channel import request, atomic, status
from connector_worker import process
from telegram_delivery import send_preview


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root/'media'
        self.media.mkdir()
        self.source = self.media/'input.mp4'
        self.source.write_bytes(b'transport fixture, not a decoded video')
        atomic(self.root/'config.json', {'inbound_roots':[str(self.media)]})

    def test_retry_same_message_is_idempotent_and_changed_payload_refused(self):
        args = dict(action='prepare', request_key='chat:message', source=str(self.source))
        first = request(args, self.root)
        self.assertEqual(request(args, self.root)['id'], first['id'])
        with self.assertRaises(ValueError):
            request(dict(args, action='library'), self.root)

    def test_symlink_escape_refused(self):
        outside = self.root/'outside.mp4';outside.write_bytes(b'private')
        (self.media/'escape.mp4').symlink_to(outside)
        with self.assertRaises(ValueError):
            request(dict(action='prepare',request_key='escape',source=str(self.media/'escape.mp4')),self.root)

    def test_transfer_mismatch_never_becomes_project(self):
        job=request(dict(action='prepare',request_key='prepare',source=str(self.source)),self.root)
        with self.assertRaises(ValueError):
            process({'workspace':str(self.root/'worker')},job,lambda _: {'base64':base64.b64encode(b'changed').decode()})

    def test_edit_preserves_original_and_invalidates_preview(self):
        job=request(dict(action='prepare',request_key='p',source=str(self.source)),self.root)
        original=self.root/'original.vproj.json';atomic(original,{'audios':[{'vol':-24}]})
        preview=self.root/'preview.mp4';preview.write_bytes(b'old export')
        cfg={'workspace':str(self.root/'worker'),'reviewed_projects':[{'source_sha256':job['source_sha256'],'project':str(original),'preview':str(preview),'preview_sha256':hashlib.sha256(preview.read_bytes()).hexdigest()}]}
        state,project=process(cfg,job,lambda _: {'base64':base64.b64encode(self.source.read_bytes()).decode()})
        atomic(self.root/'jobs'/(job['id']+'.json'),dict(job,state=state,result=project))
        edit=request(dict(action='edit',request_key='edit',parent=job['id'],edit={'music_gain_db':-28}),self.root)
        state,changed=process(cfg,edit,None)
        self.assertEqual(state,'awaiting_render_gate');self.assertIsNone(changed['preview'])
        self.assertEqual(json.loads(original.read_text())['audios'][0]['vol'],-24)
        self.assertEqual(json.loads(Path(changed['native_project']).read_text())['audios'][0]['vol'],-28)
        with self.assertRaisesRegex(ValueError,'Stale reply'):
            process(cfg,dict(edit,id='a'*32),None)

    def test_edit_needs_real_parent(self):
        with self.assertRaises(ValueError):
            request(dict(action='edit',request_key='e',edit={'music_gain_db':-25}),self.root)

    def test_uncertain_delivery_cannot_send_again(self):
        cfg={'delivery_chat_id':'123','allowed_chat_ids':['123']}
        checksum=hashlib.sha256(self.source.read_bytes()).hexdigest()
        receipt=self.root/'delivery.json'
        with mock.patch('telegram_delivery.secret_value',return_value='test-only-token'), mock.patch('urllib.request.urlopen',side_effect=TimeoutError) as opened:
            with self.assertRaises(RuntimeError):send_preview(self.source,checksum,cfg,receipt)
            with self.assertRaises(FileExistsError):send_preview(self.source,checksum,cfg,receipt)
            self.assertEqual(opened.call_count,1)
        self.assertEqual(json.loads(receipt.read_text())['state'],'started_unknown_until_confirmed')

    def test_delivery_rejects_changed_file_before_network(self):
        with mock.patch('urllib.request.urlopen') as opened:
            with self.assertRaises(ValueError):send_preview(self.source,'bad',{},self.root/'receipt.json')
            opened.assert_not_called()

    def test_actual_delivery_reply_resolves_parent_and_rejects_other_chat(self):
        atomic(self.root/'config.json',{'inbound_roots':[str(self.media)],'delivery_chat_id':'123'})
        job=request(dict(action='prepare',request_key='parent',source=str(self.source)),self.root)
        obj=dict(job,state='completed',result={'project_id':job['id'],'revision':0},delivery={'state':'sent','chat_id':123,'message_id':45,'sha256':job['source_sha256']})
        atomic(self.root/'jobs'/(job['id']+'.json'),obj)
        self.assertEqual(status(None,self.root,45)['id'],job['id'])
        self.assertEqual(status(None,self.root,source=str(self.source))['id'],job['id'])
        edit=request(dict(action='edit',request_key='reply',reply_to_message_id=45,edit={'music_gain_db':-28}),self.root)
        self.assertEqual(edit['parent'],job['id']);self.assertEqual(edit['base_revision'],0)
        obj['delivery']['chat_id']=999;atomic(self.root/'jobs'/(job['id']+'.json'),obj)
        with self.assertRaises(ValueError):status(None,self.root,45)


if __name__=='__main__':unittest.main()
