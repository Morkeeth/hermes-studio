"""studio_demo: the whole flow on a sample anyone can make. No private footage, no keys, no network.

It builds a 12-second sample clip (a colour test pattern and a synthetic voice from the system's
own text-to-speech), then runs the same alternatives and finish passes the chat uses.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

LINE = 'Welcome to Hermes Studio. Send raw footage, pick a style, and get a finished short back.'


def _voice(path):
    if shutil.which('say'):  # macOS
        aiff = Path(path).with_suffix('.aiff')
        subprocess.run(['say', '-o', str(aiff), LINE], check=True)
        return aiff
    for tts in ('espeak-ng', 'espeak'):
        if shutil.which(tts):
            wav = Path(path).with_suffix('.wav')
            subprocess.run([tts, '-w', str(wav), LINE], check=True)
            return wav
    return None


def sample(out_dir):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    clip = out / 'sample.mp4'
    voice = _voice(out / 'voice')
    audio = ['-i', str(voice)] if voice else ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1080x1920:rate=30:duration=12',
                    *audio, '-t', '12', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', str(clip)], check=True)
    return clip, bool(voice)


def run(out_dir, model=None):
    """Sample clip, then three style previews, then the finished short in style C."""
    from . import finish
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    clip, has_voice = sample(out / 'input')
    words = None
    if not (has_voice and model):
        # Without a local speech model, use the known script with even timing so the demo still runs end to end.
        ws = LINE.split(); step = 10.0 / len(ws)
        words = [{'text': w, 'start': 0.5 + i * step, 'end': 0.5 + (i + 1) * step} for i, w in enumerate(ws)]
    alts = finish.alternatives(clip, out / 'alternatives', model=model, words=words, seconds=8.0)
    final = finish.finish(clip, out / 'finished-C', model=model, words=words, style='hermes-dither')
    return {'ok': True, 'sample': str(clip), 'voice': 'system text-to-speech' if has_voice else 'none found (silent sample)',
            'transcript': 'local whisper' if words is None else 'known script, even timing (no local speech model given)',
            'alternatives': [o['preview'] for o in alts['options']], 'finished': final['output'], 'captions': final['captions']}


if __name__ == '__main__':
    import argparse, json
    p = argparse.ArgumentParser(description='Run the Hermes Studio flow on a generated sample.')
    p.add_argument('out_dir'); p.add_argument('--model', help='optional whisper.cpp model for real transcription')
    a = p.parse_args()
    print(json.dumps(run(a.out_dir, a.model), indent=1))
