import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
cx = importlib.import_module(HERE.name + '.connectors')


def make_clip(path, seconds=4, color='blue'):
    subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', f'testsrc2=size=640x360:rate=30:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
                    '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)], check=True)
    return str(path)


def sample(tmp):
    a = make_clip(Path(tmp) / 'take a.mp4')
    b = make_clip(Path(tmp) / 'take_b.mp4')
    return cx.Timeline(name='demo', cuts=[cx.Cut(a, 0.5, 1.5, 'hook'), cx.Cut(b, 1.0, 2.2, 'point'), cx.Cut(a, 2.0, 3.0, 'end')])


class RoundTrip(unittest.TestCase):
    def test_otio_round_trip_keeps_every_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            tl = sample(tmp)
            back = cx.read_otio(cx.write_otio(tl, Path(tmp) / 'demo.otio'))
            self.assertEqual(len(back.cuts), len(tl.cuts), 'a cut was dropped in the OTIO round trip')
            for a, b in zip(tl.cuts, back.cuts):
                self.assertEqual(Path(a.source).resolve(), Path(b.source).resolve())
                self.assertAlmostEqual(a.start, b.start, places=3)
                self.assertAlmostEqual(a.end, b.end, places=3)

    def test_round_trip_check_goes_red_when_a_cut_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tl = sample(tmp)
            d = cx.to_otio(tl)
            d['tracks']['children'][0]['children'].pop(1)
            self.assertNotEqual(len(cx.from_otio(d).cuts), len(tl.cuts))

    def test_real_opentimelineio_reads_our_file(self):
        try:
            import opentimelineio as otio
        except ImportError:
            self.skipTest('opentimelineio not installed')
        with tempfile.TemporaryDirectory() as tmp:
            tl = sample(tmp)
            t = otio.adapters.read_from_file(cx.write_otio(tl, Path(tmp) / 'demo.otio'))
            clips = list(t.find_clips())
            self.assertEqual(len(clips), 3)
            self.assertAlmostEqual(t.duration().to_seconds(), tl.duration, places=3)
            # and a file written by the real library comes back in
            otio.adapters.write_to_file(t, str(Path(tmp) / 'lib.otio'))
            self.assertEqual(len(cx.read_otio(Path(tmp) / 'lib.otio').cuts), 3)

    def test_bad_cut_rejected(self):
        with self.assertRaises(ValueError):
            cx.Cut('x.mp4', 2.0, 1.0)


class FCPXML(unittest.TestCase):
    def test_every_cut_in_the_spine_with_offsets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tl = sample(tmp)
            root = ET.fromstring(cx.to_fcpxml(tl).split('\n', 2)[2])
            clips = root.findall('.//spine/asset-clip')
            self.assertEqual(len(clips), 3)
            self.assertEqual(len(root.findall('.//resources/asset')), 2)  # two sources, reused
            self.assertEqual(clips[0].get('offset'), '0s')
            self.assertEqual(clips[1].get('offset'), '1s')


class Render(unittest.TestCase):
    def test_ffmpeg_render_has_the_timeline_duration_and_portrait_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            tl = sample(tmp)
            out = cx.ffmpeg_render.render(tl, Path(tmp) / 'out.mp4')
            info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', out],
                                             capture_output=True, text=True, check=True).stdout)
            v = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((int(v['width']), int(v['height'])), (1080, 1920))
            self.assertAlmostEqual(float(info['format']['duration']), tl.duration, delta=0.15)
            self.assertTrue(any(s['codec_type'] == 'audio' for s in info['streams']))

    def test_missing_source_refused(self):
        with self.assertRaises(FileNotFoundError):
            cx.ffmpeg_render.render_command(cx.Timeline('x', cuts=[cx.Cut('/nope.mp4', 0, 1)]), '/tmp/x.mp4')


class Generate(unittest.TestCase):
    def test_never_sends_even_when_spend_allowed(self):
        r = cx.generate.plan('higgsfield', 'a slow push on a desk', allow_spend=True)
        self.assertFalse(r['sent'])
        self.assertTrue(r['blocked_because'])

    def test_unknown_provider(self):
        with self.assertRaises(ValueError):
            cx.generate.plan('nope', 'x')


class Registry(unittest.TestCase):
    def test_status_is_honest(self):
        names = {c['name']: c['status'] for c in cx.describe()}
        self.assertEqual(names['higgsfield'], 'dry run')
        self.assertEqual(names['motion-canvas'], 'planned')
        self.assertEqual(names['ffmpeg'], 'working')


class Doctor(unittest.TestCase):
    def test_bare_machine_is_never_all_green(self):
        r = cx.doctor.report({'ffmpeg': None, 'ffprobe': None, 'ffmpeg_features': [], 'whisper_cli': None, 'whisper_model': None,
                              'faster_whisper': False, 'opentimelineio': False, 'video_editor_plugin': False, 'channel_config': False})
        self.assertEqual(r['summary']['ready'], 0)
        self.assertIn('FFmpeg', r['next_step'])
        gen = next(o for o in r['operations'] if 'higgsfield' in o['operation'])
        self.assertEqual(gen['status'], 'needs_setup')

    def test_full_machine_reports_ready_core_but_honest_extras(self):
        r = cx.doctor.report({'ffmpeg': '/x/ffmpeg', 'ffprobe': '/x/ffprobe', 'ffmpeg_features': ['libx264', 'loudnorm', 'freezedetect'],
                              'whisper_cli': '/x/whisper-cli', 'whisper_model': '/x/m.bin', 'faster_whisper': False,
                              'opentimelineio': True, 'video_editor_plugin': True, 'channel_config': True})
        self.assertEqual(r['summary']['ready'], 5)
        self.assertEqual(r['summary']['unavailable'], 1)

    def test_real_environment_runs_read_only(self):
        r = cx.doctor.report()
        self.assertEqual(len(r['operations']), 12)


class Finish(unittest.TestCase):
    def test_raw_to_finished_short_with_subtitles_and_intro(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = make_clip(Path(tmp) / 'raw.mp4', seconds=3)
            words = [{'text': 'Hermes', 'start': 0.2, 'end': 0.6}, {'text': 'Studio', 'start': 0.6, 'end': 1.0},
                     {'text': 'ships', 'start': 1.4, 'end': 1.8}, {'text': 'shorts.', 'start': 1.8, 'end': 2.3}]
            r = cx.finish.finish(raw, Path(tmp) / 'out', title='Test title', words=words, intro_seconds=1.0)
            info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', r['output']],
                                             capture_output=True, text=True, check=True).stdout)
            v = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((int(v['width']), int(v['height'])), (1080, 1920))
            self.assertAlmostEqual(float(info['format']['duration']), 4.0, delta=0.25)
            srt = Path(r['captions']).read_text()
            self.assertIn('HERMES STUDIO'.title().split()[0], srt)
            self.assertIn('00:00:01,200', srt)  # subtitles shifted past the intro
            self.assertEqual(r['subtitle_chunks'], 2)

    def test_chunks_carry_word_timing_for_the_highlight(self):
        c = cx.finish.chunk_words([{'text': 'a', 'start': 0.1, 'end': 0.3}, {'text': 'b', 'start': 0.3, 'end': 0.6}])
        self.assertEqual([w['start'] for w in c[0]['words']], [0.1, 0.3])
        self.assertIn('highlight', cx.styles.get('hermes-dither'))

    def test_chunks_never_linger(self):
        c = cx.finish.chunk_words([{'text': 'end', 'start': 1.0, 'end': 9.0}])
        self.assertLess(c[0]['end'], 2.2)


class StylesAndSkills(unittest.TestCase):
    def test_three_alternatives_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = make_clip(Path(tmp) / 'raw.mp4', seconds=3)
            words = [{'text': 'brain', 'start': 0.2, 'end': 0.5}, {'text': 'rot', 'start': 0.5, 'end': 0.9}]
            r = cx.finish.alternatives(raw, Path(tmp) / 'alt', title='Pick one', words=words, seconds=2.0)
            self.assertEqual([o['option'] for o in r['options']], ['A', 'B', 'C'])
            for o in r['options']:
                self.assertTrue(Path(o['preview']).is_file())

    def test_brain_rot_is_one_word_at_a_time(self):
        self.assertEqual(cx.styles.get('brain-rot')['words'], 1)
        with self.assertRaises(ValueError):
            cx.styles.get('nope')

    def test_style_from_reference_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            from PIL import Image
            p = Path(tmp) / 'ref.png'
            im = Image.new('RGB', (100, 100), (250, 240, 10)); im.paste((10, 10, 10), (0, 0, 50, 100)); im.save(p)
            st = cx.styles.from_reference(p, 'yellow')
            self.assertLess(sum(st['intro_dark']), 100)
            self.assertGreater(sum(st['intro_light']), 400)

    def test_skills_recommend_for_animation(self):
        names = [s['name'] for s in cx.skills.recommend('animate my intro and add captions')]
        self.assertIn('Remotion skills', names)
        self.assertTrue(all(s['repo'].startswith('https://github.com/') for s in cx.skills.recommend('captions')))


class ReviewFixes(unittest.TestCase):
    def test_existing_render_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = make_clip(Path(tmp) / 'raw.mp4', seconds=2)
            w = [{'text': 'one', 'start': 0.2, 'end': 0.6}]
            r = cx.finish.finish(raw, Path(tmp) / 'out', title='T', words=w, intro_seconds=0.6)
            before = Path(r['output']).read_bytes()
            with self.assertRaises(FileExistsError):
                cx.finish.finish(raw, Path(tmp) / 'out', title='Other', words=w, intro_seconds=0.6, style='brain-rot')
            self.assertEqual(Path(r['output']).read_bytes(), before)
            self.assertIn('not a native', r['kind'])
            self.assertEqual(r['audio']['streams'], 1)
            self.assertIn('mixed', r['audio']['note'])

    def test_doctor_separates_chat_from_workstation(self):
        ch = cx.doctor.report()['channel']
        self.assertIn('studio_finish', ch['workstation_only'])
        self.assertTrue(any('alternatives' in a for a in ch['callable_from_chat']))


class Demo(unittest.TestCase):
    def test_one_command_demo_runs_without_private_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = cx.demo.run(Path(tmp) / 'demo')
            self.assertTrue(Path(r['finished']).is_file())
            self.assertEqual(len(r['alternatives']), 3)
            self.assertTrue(all(Path(p).is_file() for p in r['alternatives']))


FCP_DTD = Path('/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv1_10.dtd')


class FinalCutSchema(unittest.TestCase):
    @unittest.skipUnless(FCP_DTD.is_file() and shutil.which('xmllint'), 'Final Cut Pro not installed here')
    def test_export_is_valid_against_final_cuts_own_dtd_and_the_check_can_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            dtd = Path(tmp) / 'fcpxml.dtd'; dtd.write_bytes(FCP_DTD.read_bytes())  # xmllint cannot read a DTD path with spaces
            tl = sample(tmp)
            good = Path(tmp) / 'good.fcpxml'; cx.write_fcpxml(tl, good)
            r = subprocess.run(['xmllint', '--noout', '--dtdvalid', str(dtd), str(good)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            bad = Path(tmp) / 'bad.fcpxml'; bad.write_text(good.read_text().replace('<spine>', '<spine><bogus/>'))
            r = subprocess.run(['xmllint', '--noout', '--dtdvalid', str(dtd), str(bad)], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)


if __name__ == '__main__':
    unittest.main()
