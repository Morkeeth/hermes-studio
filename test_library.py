import tempfile
from pathlib import Path
import unittest
from library import discover


class LibraryTests(unittest.TestCase):
    def test_selected_root_and_filename_search_never_infer_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'Approved blue.mp4').write_bytes(b'fixture, not a video')
            (p/'private.json').write_text('{"secret":"not media"}')
            (p/'unrelated.mp3').write_bytes(b'fixture')
            r = discover([folder], 'blue', 'video')
            self.assertEqual(r['scanned_media'], 2)
            self.assertEqual([x['name'] for x in r['results']], ['Approved blue.mp4'])
            self.assertNotIn('approved', r['results'][0])
            self.assertIn('not semantic', r['method'])

    def test_absent_root_fails_instead_of_empty_success(self):
        with self.assertRaises(FileNotFoundError):
            discover(['/does/not/exist/studio-media'])


if __name__ == '__main__':
    unittest.main()
