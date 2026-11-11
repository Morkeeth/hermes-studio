"""studio_finish: raw footage in, finished vertical short out.

One call does the whole pass, locally:
  1. word-timed transcript (whisper.cpp, existing local model; or supplied words)
  2. audio repair: rumble cut, noise reduction, loudness to -14 LUFS
  3. kinetic intro: title words slam in over a blue-dithered first frame
  4. kinetic subtitles: 1-3 word chunks pop in, the active chunk in a blue box
  5. sound design: whoosh per intro word, a sub hit on the cut into the footage
  6. optional beat: a music file ducked under the voice
Outputs final.mp4, captions.srt and finish.json (every choice, so the pass can be re-run or edited).
No downloads, no paid calls. Bring-your-own generation inserts plug in later through studio_generate.
"""
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import styles as _styles

W, H, FPS = 1080, 1920, 30
BLUE, PALE, INK = (26, 26, 240), (232, 234, 255), (255, 255, 255)
FONT_CANDIDATES = ['/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf',
                   '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf']


def _font(size):
    for f in FONT_CANDIDATES:
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _run(argv, timeout=1800):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return p.stdout


def _duration(path):
    return float(_run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)]).strip())


def audio_layout(path):
    """How many audio streams the source has. One stream means speech and music are mixed."""
    out = _run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index,channels', '-of', 'json', str(path)])
    streams = json.loads(out).get('streams', [])
    if not streams:
        return {'streams': 0, 'note': 'no audio'}
    if len(streams) == 1:
        return {'streams': 1, 'note': 'speech and any music are mixed in one track: Studio cleans and levels the whole mix, '
                                      'and cannot turn the original music down separately. A new music bed (music=...) is fully adjustable.'}
    return {'streams': len(streams), 'note': 'separate audio streams; only the first is used today'}


def _has_audio(path):
    return bool(_run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', str(path)]).strip())


# ---------- 1. words ----------

def transcribe_words(video, model, work):
    """Word timings from whisper.cpp (-ml 1 gives one token-group per segment)."""
    whisper = shutil.which('whisper-cli') or shutil.which('whisper-cpp')
    if not whisper:
        raise RuntimeError('whisper-cli not found; supply words or install whisper.cpp')
    if not model or not Path(model).is_file():
        raise RuntimeError('no local speech model; set model to an existing whisper.cpp .bin (Studio never downloads one)')
    wav = Path(work) / 'speech.wav'
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', str(wav)])
    base = Path(work) / 'words'
    _run([whisper, '-m', str(model), '-f', str(wav), '-ml', '1', '-sow', '-oj', '-of', str(base), '-np'])
    data = json.loads((base.with_suffix('.json')).read_text())
    words = []
    for seg in data.get('transcription', []):
        t = seg.get('text', '').strip()
        if not t:
            continue
        off = seg['offsets']
        words.append({'text': t, 'start': off['from'] / 1000, 'end': off['to'] / 1000})
    return words


def chunk_words(words, max_words=3, max_gap=0.35):
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w['start'] - cur[-1]['end'] > max_gap or cur[-1]['text'][-1:] in '.!?,'):
            chunks.append(cur); cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    out = []
    for c in chunks:
        start = c[0]['start']
        cap = start + 0.6 + 0.45 * len(c)  # never hold a chunk longer than it takes to read it
        out.append({'text': ' '.join(w['text'] for w in c), 'start': start, 'end': min(max(c[-1]['end'], start + 0.3), cap),
                    'words': [{'text': w['text'], 'start': w['start']} for w in c]})
    return out


FILLER = {'so', 'um', 'uh', 'okay', 'ok', 'like', 'well', 'yeah', 'and'}


def hook_title(words, max_words=6):
    """An editor's first pass at a title: the first complete sentence, filler trimmed, at most six words."""
    out = []
    for w in words:
        t = w['text'].strip()
        if not out and t.lower().strip('.,!?') in FILLER:
            continue
        out.append(t.strip('.,!?') if t[-1:] in '.!?' else t)
        if t[-1:] in '.!?' or len(out) >= max_words:
            break
    return ' '.join(out).strip(',')


def write_srt(chunks, path, offset=0.0):
    def ts(s):
        ms = int(round(max(0.0, s) * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); sec, ms = divmod(ms, 1000)
        return f'{h:02}:{m:02}:{sec:02},{ms:03}'
    lines = []
    for i, c in enumerate(chunks, 1):
        lines += [str(i), f'{ts(c["start"] + offset)} --> {ts(c["end"] + offset)}', c['text'], '']
    Path(path).write_text('\n'.join(lines))
    return str(path)


# ---------- 3+4. graphics ----------

def _dither_bg(frame_png, st=None):
    st = st or _styles.get('hermes-dither')
    g = ImageOps.autocontrast(Image.open(frame_png).convert('L'), cutoff=2)
    g = ImageOps.fit(g, (W // 3, H // 3)).convert('1').resize((W, H), Image.NEAREST).convert('L')
    return ImageOps.colorize(g, black=st['intro_dark'], white=st['intro_light'])


def _ease_out_back(x):
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def render_intro(frame_png, title, seconds, frames_dir, st=None):
    """Title words slam in one by one over the dithered first frame. Returns per-word hit times."""
    st = st or _styles.get('hermes-dither')
    bg = _dither_bg(frame_png, st)
    ink = tuple(st['text']); box = tuple(st['box']) if st['box'] is not None else (0, 0, 0)
    words = title.upper().split()
    n = max(1, len(words))
    per = min(0.35, (seconds - 0.5) / n)
    hits = [0.15 + i * per for i in range(n)]
    size = 190 if n <= 3 else 150
    f = _font(size)
    # lay out lines greedily
    lines, cur = [], []
    for i, w in enumerate(words):
        test = ' '.join(x for _, x in cur + [(i, w)])
        if cur and ImageDraw.Draw(bg).textlength(test, font=f) > W - 160:
            lines.append(cur); cur = []
        cur.append((i, w))
    lines.append(cur)
    y0 = (H - len(lines) * size * 0.95) / 2
    total = int(seconds * FPS)
    for k in range(total):
        t = k / FPS
        im = bg.copy(); d = ImageDraw.Draw(im)
        for li, line in enumerate(lines):
            x = 80
            for i, w in line:
                p = (t - hits[i]) / 0.18
                if p <= 0:
                    x += d.textlength(w + ' ', font=f); continue
                s = _ease_out_back(min(1.0, p))
                fs = _font(max(8, int(size * (0.6 + 0.4 * s))))
                ty = y0 + li * size * 0.95 + (size - fs.size) * 0.5
                ww = d.textlength(w, font=fs)
                d.rectangle([x - 12, ty - 6, x + ww + 12, ty + fs.size * 0.95], fill=box)
                d.text((x, ty), w, font=fs, fill=ink)
                x += d.textlength(w + ' ', font=f)
        im.save(Path(frames_dir) / f'i{k:05}.png')
    return hits


def render_caption_strip(chunks, duration, strip_h, frames_dir, st=None):
    """Transparent caption frames: active chunk pops in a blue box."""
    st = st or _styles.get('hermes-dither')
    base = st['size']
    total = int(math.ceil(duration * FPS))
    blank = Image.new('RGBA', (W, strip_h), (0, 0, 0, 0))
    ci = 0
    for k in range(total):
        t = k / FPS
        while ci < len(chunks) and chunks[ci]['end'] < t:
            ci += 1
        c = chunks[ci] if ci < len(chunks) and chunks[ci]['start'] <= t <= chunks[ci]['end'] else None
        if not c:
            blank.save(Path(frames_dir) / f'c{k:05}.png'); continue
        p = min(1.0, (t - c['start']) / 0.12)
        s = _ease_out_back(p)
        fs = _font(max(10, int(base * (0.75 + 0.25 * s))))
        im = blank.copy(); d = ImageDraw.Draw(im)
        text = c['text'].upper()
        tw = d.textlength(text, font=fs)
        if tw > W - 80:
            fs = _font(int(fs.size * (W - 80) / tw)); tw = d.textlength(text, font=fs)
        x = (W - tw) / 2; y = (strip_h - fs.size) / 2
        if st['box'] is not None:
            d.rounded_rectangle([x - 26, y - 14, x + tw + 26, y + fs.size + 10], radius=20, fill=tuple(st['box']) + (int(235 * p),))
        ws = c.get('words') or []
        if st.get('highlight') and len(ws) > 1:
            # karaoke: the word being spoken lights up; the rest of the chunk stays readable but quieter
            cx = x
            active = max((i for i, w in enumerate(ws) if w['start'] <= t), default=0)
            for i, w in enumerate(ws):
                word = w['text'].upper()
                ww = d.textlength(word, font=fs)
                if i == active:
                    d.rounded_rectangle([cx - 8, y - 4, cx + ww + 8, y + fs.size + 2], radius=12, fill=tuple(st['highlight']) + (int(255 * p),))
                    d.text((cx, y), word, font=fs, fill=tuple(st.get('highlight_text', st['text'])) + (int(255 * p),))
                else:
                    d.text((cx, y), word, font=fs, fill=tuple(st['text']) + (int(170 * p),))
                cx += ww + d.textlength(' ', font=fs)
        else:
            d.text((x, y), text, font=fs, fill=tuple(st['text']) + (int(255 * p),))
        im.save(Path(frames_dir) / f'c{k:05}.png')
    return total


# ---------- 5. sound design ----------

def render_sfx(hits, cut_at, total, path):
    """Whoosh (filtered noise swell) before each intro hit, a sub hit on the cut into the footage."""
    parts, labels = [], []
    for i, h in enumerate(hits):
        start = max(0.0, h - 0.18)
        parts.append(f"anoisesrc=d=0.3:c=pink:r=48000:a=0.5,highpass=f=900,lowpass=f=6000,afade=t=in:d=0.18,afade=t=out:st=0.18:d=0.12,"
                     f"volume=0.35,adelay={int(start * 1000)}|{int(start * 1000)}[w{i}]")
        labels.append(f'[w{i}]')
    parts.append(f"sine=f=52:d=0.6:r=48000,volume=0.9,afade=t=out:st=0.05:d=0.55,adelay={int(cut_at * 1000)}|{int(cut_at * 1000)}[hit]")
    labels.append('[hit]')
    parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0,apad=whole_dur={total},atrim=0:{total}[out]")
    _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-filter_complex', ';'.join(parts), '-map', '[out]', '-ac', '2', str(path)])
    return str(path)


# ---------- the pass ----------

AUDIO_FIX = 'highpass=f=80,afftdn=nf=-25,loudnorm=I=-14:TP=-1.5:LRA=11'


def finish(raw, out_dir, title='', model=None, words=None, music=None, intro_seconds=1.6, music_db=-20, style='hermes-dither', max_seconds=None, overwrite=False, start_at=0.0, broll=None):
    raw = Path(raw).expanduser().resolve(strict=True)
    out = Path(out_dir).expanduser(); out.mkdir(parents=True, exist_ok=True)
    if (out / 'final.mp4').exists() and not overwrite:
        raise FileExistsError(f'{out} already holds a render; use a new output directory (earlier versions are never overwritten)')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        st = _styles.get(style)
        if words is None:
            words = transcribe_words(raw, model, tmp) if _has_audio(raw) else []
        all_words = words
        original = raw
        if start_at and start_at > 0:
            # tighten the opening: cut the first seconds, keep word timing in step
            trimmed = tmp / 'trimmed.mp4'
            _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{start_at:.3f}', '-i', str(raw), '-c:v', 'libx264', '-crf', '16',
                  '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(trimmed)])
            raw = trimmed
            words = [dict(w, start=w['start'] - start_at, end=w['end'] - start_at) for w in words if w['start'] >= start_at]
        dur = _duration(raw) if not max_seconds else min(_duration(raw), max_seconds)
        words = [w for w in words if w['start'] < dur]
        chunks = chunk_words(words, max_words=st['words'])
        title = title or hook_title(words) or 'HERMES STUDIO'
        _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(min(0.5, dur / 2)), '-i', str(raw), '-frames:v', '1', str(tmp / 'first.png')])
        (tmp / 'intro').mkdir(); (tmp / 'caps').mkdir()
        hits = render_intro(tmp / 'first.png', title, intro_seconds, tmp / 'intro', st)
        strip_h = max(300, int(st['size'] * 2.2))
        render_caption_strip(chunks, dur, strip_h, tmp / 'caps', st)
        cap_y = int(H * st['y'] - strip_h / 2)
        total = intro_seconds + dur
        render_sfx(hits, intro_seconds, total, tmp / 'sfx.wav')
        # voice: repaired footage audio, delayed past the intro
        inputs = ['-framerate', str(FPS), '-i', str(tmp / 'intro' / 'i%05d.png'),
                  '-t', f'{dur:.3f}', '-i', str(raw),
                  '-framerate', str(FPS), '-i', str(tmp / 'caps' / 'c%05d.png'),
                  '-i', str(tmp / 'sfx.wav')]
        d_ms = int(intro_seconds * 1000)
        split = bool(st.get('split'))
        nxt = 4
        if split:
            # brain-rot split screen: the speaker on top, looping B-roll below, captions on the seam.
            # No B-roll configured: generate our own, a Game of Life in the style colours (rights-free moving dither).
            if broll:
                inputs += ['-stream_loop', '-1', '-i', str(Path(broll).expanduser())]
            else:
                dark = '#%02x%02x%02x' % tuple(st['intro_dark']); light = '#%02x%02x%02x' % tuple(st['intro_light'])
                inputs += ['-f', 'lavfi', '-i', f'life=s=135x120:mold=6:r={FPS}:ratio=0.22:death_color={dark}:life_color={light}:mold_color={dark}']
            b_idx, nxt = nxt, nxt + 1
            top = (f"[1:v]scale={W}:{H // 2}:force_original_aspect_ratio=increase,crop={W}:{H // 2},fps={FPS},setsar=1[top];"
                   f"[{b_idx}:v]scale={W}:{H // 2}:force_original_aspect_ratio=increase:flags=neighbor,crop={W}:{H // 2},fps={FPS},setsar=1,trim=0:{dur:.3f},setpts=PTS-STARTPTS[bot];"
                   f"[top][bot]vstack=inputs=2[foot];")
        else:
            top = f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[foot];"
        v = (top +
             f"[foot][2:v]overlay=0:{cap_y}:format=auto[capd];"
             f"[0:v]fps={FPS},setsar=1,format=yuv420p[intro];[capd]format=yuv420p[body];"
             f"[intro][body]concat=n=2:v=1:a=0[v]")
        if _has_audio(raw):
            a = f"[1:a]{AUDIO_FIX},aresample=48000,adelay={d_ms}|{d_ms},apad=whole_dur={total}[voice];"
        else:
            a = f"anullsrc=r=48000:cl=stereo,atrim=0:{total}[voice];"
        mix = ['[voice]', '[3:a]']
        if music:
            inputs += ['-stream_loop', '-1', '-i', str(Path(music).expanduser())]
            a += (f"[{nxt}:a]aresample=48000,volume={music_db}dB,atrim=0:{total}[mus];"
                  f"[voice]asplit=2[voice][key];[mus][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];")
            mix.append('[duck]')
        a += f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0,alimiter=limit=0.95[a]"
        final = out / 'final.mp4'
        _run(['ffmpeg', '-nostdin', '-v', 'error', '-y', *inputs, '-filter_complex', v + ';' + a,
              '-map', '[v]', '-map', '[a]', '-t', f'{total:.3f}', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
              '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(final)])
        srt = write_srt(chunks, out / 'captions.srt', offset=intro_seconds)
        recipe = {'source': str(original), 'start_at': start_at, 'broll': str(broll) if broll else ('generated: game of life' if st.get('split') else None), 'words': all_words, 'title': title, 'style': style if isinstance(style, str) else st, 'intro_seconds': intro_seconds, 'audio_fix': AUDIO_FIX,
                  'music': str(music) if music else None, 'music_db': music_db, 'chunks': chunks,
                  'sound_design': {'whoosh_at': hits, 'sub_hit_at': intro_seconds}, 'output': str(final)}
        (out / 'finish.json').write_text(json.dumps(recipe, indent=1))
    return {'ok': True, 'output': str(final), 'captions': srt, 'recipe': str(out / 'finish.json'),
            'kind': 'rendered short + re-runnable recipe (finish.json); not a native .vproj editor project',
            'audio': audio_layout(original),
            'seconds': round(_duration(final), 2), 'subtitle_chunks': len(chunks)}


def alternatives(raw, out_dir, title='', model=None, words=None, styles=('clean-short', 'brain-rot', 'hermes-dither'), seconds=10.0, music_beds=None, broll=None):
    """Render the same opening in several styles so Hermes can offer A/B/C in the chat."""
    raw = Path(raw).expanduser().resolve(strict=True)
    out = Path(out_dir).expanduser(); out.mkdir(parents=True, exist_ok=True)
    if words is None:
        with tempfile.TemporaryDirectory() as tmp:
            words = transcribe_words(raw, model, tmp) if _has_audio(raw) else []
    options = []
    for letter, name in zip('ABCDEFG', styles):
        bed = (music_beds or {}).get(name)
        r = finish(raw, out / f'{letter}-{name}', title=title, words=words, style=name, max_seconds=seconds, intro_seconds=1.2,
                   music=bed, music_db=_styles.get(name).get('music_db', -20), broll=broll)
        options.append({'option': letter, 'style': name, 'label': _styles.get(name)['label'], 'preview': r['output']})
    return {'ok': True, 'question': 'Pick a style: ' + ', '.join(f"{o['option']} {o['label']}" for o in options), 'options': options,
            'title': title or hook_title(words), 'transcript': ' '.join(w['text'] for w in words)[:400],
            'editor_note': editor_note(words)}


def insert_shot(finished, shot, out_dir, seconds=3.0):
    """Put a generated shot at the start of a finished short, as a new version. Nothing is overwritten."""
    out = Path(out_dir).expanduser(); out.mkdir(parents=True, exist_ok=True)
    dest = out / 'final.mp4'
    if dest.exists():
        raise FileExistsError(f'{out} already holds a render')
    with tempfile.TemporaryDirectory() as tmp:
        sfx = Path(tmp) / 'hit.wav'
        render_sfx([max(0.2, seconds - 0.2)], seconds, seconds + 0.01, sfx)
        v = (f"[0:v]trim=0:{seconds},setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1,format=yuv420p[g];"
             f"[1:v]fps={FPS},setsar=1,format=yuv420p[f];[g][f]concat=n=2:v=1:a=0[v];"
             f"[2:a]atrim=0:{seconds},aresample=48000[ga];[1:a]aresample=48000[fa];[ga][fa]concat=n=2:v=0:a=1[a]")
        _run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(shot), '-i', str(finished), '-i', str(sfx),
              '-filter_complex', v, '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p',
              '-c:a', 'aac', '-movflags', '+faststart', str(dest)])
    return {'ok': True, 'output': str(dest), 'seconds': round(_duration(dest), 2)}


def editor_note(words, duration=None):
    """An editor's first read of the footage, from what can be measured: pace, hook timing, length.
    It recommends one style and says why. It is a starting opinion, not a taste judgement."""
    if not words:
        return {'pick': 'clean-short', 'why': 'No speech found, so a calm clean short lets the picture carry it.'}
    spoken = max(0.1, words[-1]['end'] - words[0]['start'])
    pace = len(words) / spoken
    first = words[0]['start']
    hook = hook_title(words)
    if pace >= 2.9:
        pick, why = 'brain-rot', f'You talk fast ({pace:.1f} words a second), so one big word at a time keeps up with you.'
    elif first > 1.5:
        pick, why = 'hermes-dither', f'The first words come late ({first:.1f} s in), so a bold kinetic intro carries the opening.'
    else:
        pick, why = 'clean-short', f'Steady pace ({pace:.1f} words a second) and an early start, so clean captions let the words land.'
    return {'pick': pick, 'why': why, 'hook': hook, 'pace_wps': round(pace, 2), 'first_word_s': round(first, 2)}
