import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('workflow_core',HERE/'workflow.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)

class WorkflowTests(unittest.TestCase):
    def prepare(self,tmp,**extra):
        source=Path(tmp)/'source.mp4';source.write_bytes(b'input')
        args=dict(source=source,start=10,end=30,title='A local book club',captions=[{'start':11,'end':13,'text':'My agents read the same thing.'}],output_dir=Path(tmp)/'job')
        args.update(extra);return w.prepare_cut(**args)
    def test_selected_source_times_shift_to_short(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            plan=self.prepare(tmp)
            self.assertIn('00:00:01,000 --> 00:00:03,000',Path(plan['captions']).read_text())
            self.assertEqual([s['tool'] for s in plan['steps']],['lk_trim','lk_crop_to_aspect','lk_burn_captions','lk_add_text','lk_normalize_loudness'])
            self.assertEqual(plan['steps'][2]['args']['outline_px'],0)
            self.assertFalse(plan['steps'][3]['args']['box'])
    def test_reject_empty_caption_interval_before_job(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            with self.assertRaises(ValueError):self.prepare(tmp,captions=[{'start':0,'end':1,'text':'outside'}])
            self.assertFalse((Path(tmp)/'job').exists())
    def test_invalid_duration(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            with self.assertRaises(ValueError):self.prepare(tmp,end=11)
    def test_failure_stops_next_edit(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            plan=self.prepare(tmp);calls=[]
            def dispatch(name,args):calls.append(name);return json.dumps({'ok':False,'error':'editor missing'})
            with mock.patch.dict('os.environ',{'HELICON_RENDER_BRIEF':'mocked-dispatch-only'}),self.assertRaises(RuntimeError):w.execute_cut(plan,dispatch)
            self.assertEqual(calls,['lk_trim'])
            self.assertEqual(len(json.loads((Path(plan['captions']).parent/'editor-calls.json').read_text())),1)
    def test_chain_uses_actual_outputs(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            plan=self.prepare(tmp);calls=[]
            def dispatch(name,args):
                if calls:self.assertEqual(args['input'],calls[-1])
                output=Path(args['output']);output.write_bytes(b'video');calls.append(str(output));return json.dumps({'ok':True,'output':str(output)})
            with mock.patch.dict('os.environ',{'HELICON_RENDER_BRIEF':'mocked-dispatch-only'}):result=w.execute_cut(plan,dispatch)
            self.assertTrue(result['ok']);self.assertTrue(result['review_required']);self.assertEqual(len(calls),5)
    def test_unique_job_never_overwrites(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            self.prepare(tmp)
            with self.assertRaises(FileExistsError):self.prepare(tmp)
    def test_ungated_request_never_invokes_editor(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp,mock.patch.dict('os.environ',{},clear=True):
            plan=self.prepare(tmp);dispatch=mock.Mock()
            with self.assertRaises(ValueError):w.execute_cut(plan,dispatch)
            dispatch.assert_not_called()

if __name__=='__main__':unittest.main()
