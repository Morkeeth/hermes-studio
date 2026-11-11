"""studio_doctor: a read-only, honest capability report for this machine.

Hermes should call this first when a user asks for anything video. It never
installs, downloads or spends. Every operation is reported as one of:
ready, needs_setup, experimental, unavailable, with the exact blocker.
"""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

READY, SETUP, EXPERIMENTAL, UNAVAILABLE = 'ready', 'needs_setup', 'experimental', 'unavailable'


def _ffmpeg_filters(ffmpeg):
    try:
        out = subprocess.run([ffmpeg, '-hide_banner', '-filters'], capture_output=True, text=True, timeout=20).stdout
        enc = subprocess.run([ffmpeg, '-hide_banner', '-encoders'], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return set()
    have = {name for name in ('loudnorm', 'drawtext', 'freezedetect') if f' {name} ' in out}
    if 'libx264' in enc:
        have.add('libx264')
    return have


def _hermes_home():
    return Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')).expanduser()


def environment(which=shutil.which):
    ffmpeg, ffprobe = which('ffmpeg'), which('ffprobe')
    whisper = which('whisper-cli') or which('whisper-cpp')
    model = os.environ.get('HERMES_STUDIO_WHISPER_MODEL', '')
    home = _hermes_home()
    return {
        'ffmpeg': ffmpeg, 'ffprobe': ffprobe,
        'ffmpeg_features': sorted(_ffmpeg_filters(ffmpeg)) if ffmpeg else [],
        'whisper_cli': whisper,
        'whisper_model': model if model and Path(model).is_file() else None,
        'faster_whisper': importlib.util.find_spec('faster_whisper') is not None,
        'opentimelineio': importlib.util.find_spec('opentimelineio') is not None,
        'video_editor_plugin': (home / 'plugins' / 'hermes-video-editor').exists(),
        'channel_config': (home / 'studio-channel' / 'config.json').is_file(),
    }


def report(env=None):
    env = env if env is not None else environment()
    feats = set(env.get('ffmpeg_features') or [])
    has_ff = bool(env.get('ffmpeg') and env.get('ffprobe'))
    speech = bool((env.get('whisper_cli') and env.get('whisper_model')) or env.get('faster_whisper'))
    ops = []

    def op(name, status, needs=None, note=''):
        ops.append({'operation': name, 'status': status, 'blockers': needs or [], 'note': note})

    ff_block = [] if has_ff else ['install FFmpeg and ffprobe']
    op('render a timeline (FFmpeg)', READY if has_ff and 'libx264' in feats else SETUP,
       ff_block + ([] if 'libx264' in feats or not has_ff else ['FFmpeg build without libx264']))
    op('check a finished short (studio_gate)', READY if has_ff and {'loudnorm', 'freezedetect'} <= feats else SETUP,
       ff_block + [f'FFmpeg filter {f} missing' for f in ('loudnorm', 'freezedetect') if has_ff and f not in feats])
    op('transcribe a take (studio_take)', READY if speech else SETUP,
       [] if speech else ['an existing local speech model: set HERMES_STUDIO_WHISPER_MODEL to a whisper.cpp model, or install faster-whisper'],
       'Studio never downloads a model.')
    op('editable cut with animated captions (studio_cut, studio_build)', READY if env.get('video_editor_plugin') and has_ff else SETUP,
       ([] if env.get('video_editor_plugin') else ['hermes plugins install oliverhees/hermes-video-editor (PolyForm Noncommercial)']) + ff_block)
    op('reply to a delivered video to change it (studio_request)', READY if env.get('channel_config') else SETUP,
       [] if env.get('channel_config') else ['create HERMES_HOME/studio-channel/config.json from GATEWAY.example.json'],
       'Conversation edits support music level today. Revisions wait at the render gate.')
    op('export to Final Cut or DaVinci Resolve (FCPXML)', EXPERIMENTAL, [], 'Cuts only; not yet opened in the editors.')
    op('OpenTimelineIO export and import', EXPERIMENTAL, [],
       'One video track of cuts; captions and music not carried.' + ('' if env.get('opentimelineio') else ' Works without the opentimelineio package.'))
    op('generate a shot with a video model (Higgsfield)', UNAVAILABLE, ['no provider is wired; studio_generate is a dry run'],
       'Paid generation will always need the owner\'s approval.')
    op('motion engines (Motion Canvas, Revideo, Manim)', UNAVAILABLE, ['not built yet'])

    counts = {s: sum(1 for o in ops if o['status'] == s) for s in (READY, SETUP, EXPERIMENTAL, UNAVAILABLE)}
    first = next((o for o in ops if o['status'] == SETUP), None)
    return {'summary': counts, 'operations': ops, 'environment': env,
            'next_step': (f"To unlock '{first['operation']}': {first['blockers'][0]}" if first and first['blockers'] else 'Core local workflow is ready.')}
