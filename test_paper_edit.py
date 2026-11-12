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
    def test_word_times_refine_rendered_chunks(self):
        # Shape written by finish(): source words, plus rendered chunks that carry their word starts.
        recipe = {'words': [{'text': 'Hi.', 'start': 0.0, 'end': 0.4}, {'text': 'Bye.', 'start': 1.5, 'end': 1.9}],
                  'chunks': [{'text': 'Hi.', 'start': 0.0, 'end': 1.0, 'words': [{'text': 'Hi.', 'start': 0.0}]},
                             {'text': 'Bye.', 'start': 1.5, 'end': 2.0, 'words': [{'text': 'Bye.', 'start': 1.5}]}]}
        self.assertEqual([s['parts'] for s in pe.sentences(recipe)], [['Hi.'], [1.1, 'Bye.']])

    def test_trimmed_footage_never_appears(self):
        # Codex P1: finish() keeps every source word in 'words' but renders only from start_at to the end of the take.
        recipe = {'start_at': 5.0,
                  'words': [{'text': 'Excluded.', 'start': 0.0, 'end': 0.5}, {'text': 'Kept.', 'start': 5.0, 'end': 5.5},
                            {'text': 'Beyond.', 'start': 10.0, 'end': 10.5}],
                  'chunks': [{'text': 'Kept.', 'start': 0.0, 'end': 0.9, 'words': [{'text': 'Kept.', 'start': 0.0}]}]}
        text = pe.paper_edit(recipe)
        self.assertIn('0.0 s  Kept.', text)
        self.assertNotIn('Excluded', text)
        self.assertNotIn('Beyond', text)
        self.assertEqual(pe.sentences(recipe)[0]['end'], 0.5)

    def test_sentence_ends_before_a_closing_quote(self):
        # Codex P2
        recipe = {'chunks': [{'text': 'Hello."', 'start': 0, 'end': 1}, {'text': 'Bye.', 'start': 2, 'end': 3}]}
        self.assertEqual(pe.paper_edit(recipe).split('\n')[1:5], ['HOOK', '0.0 s  Hello."', 'PAYOFF', '2.0 s  [gap 1.0 s] Bye.'])

    def test_gap_must_be_over_the_threshold(self):
        recipe = {'chunks': [{'text': 'one', 'start': 0, 'end': 1}, {'text': 'two.', 'start': 1.5, 'end': 2}]}
        self.assertNotIn('[gap', pe.paper_edit(recipe))

    def test_small_limit_is_honoured_or_refused(self):
        recipe = {'chunks': [{'text': 'A sentence that is long enough.', 'start': 0, 'end': 1}]}
        self.assertLessEqual(len(pe.paper_edit(recipe, limit=len(pe.NOTE) + 20)), len(pe.NOTE) + 20)
        with self.assertRaises(ValueError):
            pe.paper_edit(recipe, limit=100)

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
