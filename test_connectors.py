import importlib
import json
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
        self.assertEqual(len(r['operations']), 10)


if __name__ == '__main__':
    unittest.main()
