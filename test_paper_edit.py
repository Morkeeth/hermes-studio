import importlib.util
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('paper_edit', HERE / 'connectors' / 'paper_edit.py')
pe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pe)
REAL = json.loads((HERE / 'fixtures' / 'finish-2026-11-11.json').read_text())


class RealRecipe(unittest.TestCase):
    """The finished C short from the 11 Nov Telegram run. Its recipe keeps caption chunks, not words."""

    def test_full_message(self):
        self.assertEqual(pe.paper_edit(REAL), '\n'.join([
            'PAPER EDIT · So welcome to Hermes',
            'HOOK',
            '0.0 s  So welcome to [gap 2.0 s] Hermes Studio.',
            'TURN',
            "5.1 s  Let's see what animations we can do and let's spin up the good old slop cannon.",
            "10.0 s  Let's see what you can do.",
            'PAYOFF',
            '12.0 s  Over to you Hermes.',
            pe.NOTE]))

    def test_hook_gap_is_measured_from_caption_times(self):
        hook = pe.sentences(REAL)[0]
        self.assertEqual(hook['parts'], ['So welcome to', 2.05, 'Hermes Studio.'])

    def test_every_word_survives_in_order(self):
        said = ' '.join(c['text'] for c in REAL['chunks'])
        shown = ' '.join(p for s in pe.sentences(REAL) for p in s['parts'] if isinstance(p, str))
        self.assertEqual(shown, said)

    def test_caller_beats_override_position(self):
        text = pe.paper_edit(REAL, beats=['HOOK', 'HOOK', 'TURN', 'PAYOFF'])
        self.assertIn('HOOK\n0.0 s  So welcome to [gap 2.0 s] Hermes Studio.\n5.1 s  Let', text)
        with self.assertRaises(ValueError):
            pe.paper_edit(REAL, beats=['HOOK'])


class Shapes(unittest.TestCase):
    def test_word_times_win_over_chunks(self):
        recipe = {'words': [{'text': 'Hi.', 'start': 0.0, 'end': 0.4}, {'text': 'Bye.', 'start': 1.5, 'end': 1.9}],
                  'chunks': [{'text': 'ignored', 'start': 0, 'end': 2}]}
        self.assertEqual([s['parts'] for s in pe.sentences(recipe)], [['Hi.'], [1.1, 'Bye.']])

    def test_short_gaps_are_not_marked(self):
        recipe = {'chunks': [{'text': 'one', 'start': 0, 'end': 1}, {'text': 'two.', 'start': 1.3, 'end': 2}]}
        self.assertNotIn('[gap', pe.paper_edit(recipe))

    def test_empty_recipe_says_so(self):
        self.assertIn('no paper edit', pe.paper_edit({'chunks': []}))

    def test_fits_one_telegram_message(self):
        recipe = {'chunks': [{'text': f'word{i}.', 'start': i, 'end': i + 0.5} for i in range(2000)]}
        text = pe.paper_edit(recipe)
        self.assertLessEqual(len(text), pe.TELEGRAM_LIMIT)
        self.assertTrue(text.endswith(pe.NOTE))

    def test_title_in_header(self):
        self.assertTrue(pe.paper_edit({'title': 'My agents', 'chunks': [{'text': 'x.', 'start': 0, 'end': 1}]}).startswith('PAPER EDIT · My agents'))


if __name__ == '__main__':
    unittest.main()
