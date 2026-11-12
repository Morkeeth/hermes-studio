"""The paper edit: a finished short's words as a plain text timeline.

Pure function, no I/O. It reads a finish.json recipe (already loaded as a
dict) and returns the text Hermes can send after each video:

    PAPER EDIT
    HOOK
    0.0 s  So welcome to [gap 2.0 s] Hermes Studio.
    TURN
    4.0 s  Let's see what animations ...
    PAYOFF
    12.0 s  Over to you Hermes.

Times are seconds into the take (before the intro). Gaps come from caption
timing, which can run longer or shorter than the real pause in the audio,
and the message says so. Beat labels are by position unless the caller
passes its own: the first sentence is the hook, the last is the payoff,
everything between is the turn.
"""

TELEGRAM_LIMIT = 4096
NOTE = ('Gaps come from caption timing and can differ from the real pause. '
        'Labels are by position: first sentence, middle, last. Quote a line to change it.')


def _units(recipe):
    """Timed text units: word-level when the recipe keeps words, else caption chunks."""
    words = recipe.get('words') or []
    if words and all('start' in w and 'end' in w for w in words):
        return [{'text': str(w['text']).strip(), 'start': float(w['start']), 'end': float(w['end'])} for w in words]
    units = []
    for c in recipe.get('chunks') or []:
        units.append({'text': str(c['text']).strip(), 'start': float(c['start']), 'end': float(c['end'])})
    return [u for u in units if u['text']]


def sentences(recipe, min_gap=0.5):
    """Group units into sentences. Each sentence is {'start', 'end', 'parts'}; a part is text or a gap in seconds."""
    out, cur, prev_end = [], None, None
    for u in sorted(_units(recipe), key=lambda u: u['start']):
        if cur is None:
            cur = {'start': u['start'], 'end': u['end'], 'parts': []}
        if prev_end is not None and u['start'] - prev_end >= min_gap:
            cur['parts'].append(round(u['start'] - prev_end, 2))
        cur['parts'].append(u['text'])
        cur['end'] = u['end']
        prev_end = u['end']
        if u['text'][-1:] in '.!?':
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    return out


def default_beats(n):
    if n <= 0:
        return []
    if n == 1:
        return ['HOOK']
    return ['HOOK'] + ['TURN'] * (n - 2) + ['PAYOFF']


def paper_edit(recipe, beats=None, min_gap=0.5, limit=TELEGRAM_LIMIT):
    """Plain text for one Telegram message. beats: optional label per sentence (same length as the sentences)."""
    sents = sentences(recipe, min_gap=min_gap)
    if not sents:
        return 'PAPER EDIT\nNo words in this recipe, so there is no paper edit to show.'
    labels = list(beats) if beats is not None else default_beats(len(sents))
    if len(labels) != len(sents):
        raise ValueError(f'{len(labels)} beat labels for {len(sents)} sentences')
    head = 'PAPER EDIT' + (f" · {recipe['title']}" if recipe.get('title') else '')
    lines, last = [head], None
    for label, s in zip(labels, sents):
        if label != last:
            lines.append(str(label).upper())
            last = label
        body = ' '.join(f'[gap {p:.1f} s]' if isinstance(p, float) else p for p in s['parts'])
        lines.append(f"{s['start']:.1f} s  {body}")
    lines.append(NOTE)
    text = '\n'.join(lines)
    if len(text) > limit:
        cut = limit - len(NOTE) - 3
        text = text[:cut].rsplit('\n', 1)[0] + '\n…\n' + NOTE
    return text
