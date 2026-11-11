import importlib.util
import json
from email.message import Message
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch, MagicMock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('studio_core',HERE/'studio.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)

class PostTests(unittest.TestCase):
    def response(self, body=b'<title>A &amp; B</title>', status=200):
        r=MagicMock();r.__enter__.return_value=r;r.status=status;r.geturl.return_value='https://example.org/post'
        headers=Message();headers['Content-Type']='text/html; charset=utf-8';r.headers=headers;r.read.return_value=body
        return r
    @patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))])
    @patch.object(s.urllib.request,'build_opener')
    def test_public_title(self, opener, dns):
        opener.return_value.open.return_value=self.response()
        result=s.studio_verify_post('https://example.org/post')
        self.assertTrue(result['exists']);self.assertEqual(result['title'],'A & B')
    @patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))])
    @patch.object(s.urllib.request,'build_opener')
    def test_missing_page(self, opener, dns):
        opener.return_value.open.return_value=self.response(status=404)
        self.assertFalse(s.studio_verify_post('https://example.org/post')['exists'])
    @patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))])
    @patch.object(s.urllib.request,'build_opener')
    def test_no_title(self, opener, dns):
        opener.return_value.open.return_value=self.response(b'<html>login</html>')
        self.assertFalse(s.studio_verify_post('https://example.org/post')['exists'])
    @patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))])
    def test_private_address_rejected(self,dns):
        self.assertFalse(s.studio_verify_post('https://example.org/post')['exists'])
    def test_non_http_rejected(self):
        self.assertFalse(s.studio_verify_post('file:///etc/hosts')['exists'])
    @patch.object(s.socket,'getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',80))])
    def test_private_redirect_rejected(self,dns):
        with self.assertRaises(ValueError):s.PublicRedirect().redirect_request(None,None,302,'',{},'http://localhost/a')

class GateTests(unittest.TestCase):
    def test_gate_failure_modes(self):
        # Exercise actual decision function with known probe/filter evidence.
        info={'streams':[{'codec_type':'video','width':1080,'height':1920},{'codec_type':'audio'}],'format':{'duration':'20'}}
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            p=Path(tmp)/'video';p.write_bytes(b'video');r=Path(tmp)/'review.json'
            evidence={'sha256':s.digest(p),'reviewer':'unit test','method':'fixture','title_on_first_frame':True,'captions_visible':True,'no_plain_zoom':True};r.write_text(json.dumps(evidence))
            def run(command,*args):
                if command[0]=='ffprobe':return json.dumps(info),''
                if '-af' in command:return '', '{"input_i":"-14"}'
                return '',''
            with patch.object(s,'run',side_effect=run):
                self.assertTrue(s.studio_gate(p,r)['pass'])
                for key in ('title_on_first_frame','captions_visible','no_plain_zoom'):
                    evidence[key]=False;r.write_text(json.dumps(evidence))
                    self.assertFalse(s.studio_gate(p,r)['checks'][key])
                    evidence[key]=True
                r.write_text(json.dumps(evidence))
                self.assertFalse(s.studio_gate(p)['pass'])
                evidence['sha256']='stale';r.write_text(json.dumps(evidence))
                self.assertFalse(s.studio_gate(p,r)['visual_review_bound'])
                evidence['sha256']=s.digest(p);r.write_text(json.dumps(evidence))
                info['streams'][0]['width']=1920
                self.assertFalse(s.studio_gate(p,r)['checks']['portrait_9_16'])
                info['format']['duration']='14'
                self.assertFalse(s.studio_gate(p,r)['checks']['duration_15_60'])
    def test_trailing_freeze_is_failure(self):
        info={'streams':[{'codec_type':'video','width':1080,'height':1920}],'format':{'duration':'20'}}
        with tempfile.NamedTemporaryFile(dir=HERE) as p:
            with patch.object(s,'run',side_effect=[(json.dumps(info),''),('','freeze_start: 18.5')]):
                result=s.studio_gate(p.name)
                self.assertFalse(result['checks']['no_static_hold_over_1s']);self.assertEqual(result['freeze_intervals'],[[18.5,20]])
    def test_rank_reason(self):
        hooks=s.rank_hooks([{'start':0,'end':3,'text':'Um here is the thing we made'},{'start':4,'end':8,'text':'Why did this actually fail for us?'}])
        self.assertEqual(hooks[0]['start'],4);self.assertIn('question',hooks[0]['reason'])

class PluginTests(unittest.TestCase):
    def test_register_and_invoke_agent_tool(self):
        spec=importlib.util.spec_from_file_location('studio_plugin_test',HERE/'__init__.py',submodule_search_locations=[str(HERE)])
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        ctx=MagicMock();module.register(ctx)
        ctx.register_skill.assert_called_once()
        registered={c.kwargs['name']:c.kwargs for c in ctx.register_tool.call_args_list}
        self.assertEqual(set(registered),{'studio_take','studio_cut','studio_gate','studio_verify_post','studio_library','studio_build','studio_request','studio_status'})
        result=json.loads(registered['studio_gate']['handler']({'video':str(HERE/'missing.mp4')}))
        self.assertFalse(result['pass']);self.assertTrue(result['reasons'])

@unittest.skipUnless(os.getenv('STUDIO_REAL_FIXTURES')=='1','Set STUDIO_REAL_FIXTURES=1 for real FFmpeg media checks')
class RealFixtures(unittest.TestCase):
    def test_real_finished_short(self):
        config=json.loads((HERE/'evidence/fixtures.json').read_text())
        result=s.studio_gate(config['pass'],HERE/'evidence/pass-review.json')
        (HERE/'evidence/pass-gate.json').write_text(json.dumps(result,indent=2))
        self.assertTrue(result['pass'],result['reasons'])
    def test_real_failed_still(self):
        config=json.loads((HERE/'evidence/fixtures.json').read_text())
        result=s.studio_gate(config['fail'])
        (HERE/'evidence/fail-gate.json').write_text(json.dumps(result,indent=2))
        self.assertFalse(result['pass']);self.assertFalse(result['checks']['no_static_hold_over_1s'])
    def test_real_transcript(self):
        result=json.loads((HERE/'evidence/takes/takes.json').read_text())
        self.assertEqual(result['backend'],'whisper.cpp');self.assertTrue(result['hooks']);self.assertTrue(any('book club' in s['text'] for s in result['segments']))

if __name__=='__main__':unittest.main()
