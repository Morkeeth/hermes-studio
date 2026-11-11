"""One local job: raw take -> script -> library -> editable animation/cut -> export.

The template carries reviewed composition and real aligned words. No LLM, model
download, paid generation, publishing or invented timing is part of this runner.
"""
import importlib
import json
import os
from pathlib import Path
import sys
import types

from .library import discover
from .studio import digest, transcribe_folder, studio_gate


def editor_api():
    from hermes_constants import get_hermes_home
    root = Path(get_hermes_home()) / 'plugins/hermes-video-editor'
    if not root.is_dir():
        raise ValueError('Install and enable the separately licensed hermes-video-editor first')
    pkg = types.ModuleType('studio_native_editor')
    pkg.__path__ = [str(root)]
    sys.modules[pkg.__name__] = pkg
    return importlib.import_module(pkg.__name__ + '.editor.project')


def build(recipe_path, output_dir, render=False):
    recipe_path = Path(recipe_path).expanduser().resolve(strict=True)
    spec = json.loads(recipe_path.read_text())
    source = Path(spec['source']).resolve(strict=True)
    if digest(source) != spec['source_sha256']:
        raise ValueError('Original source differs from the aligned/approved recipe')
    out = Path(output_dir).expanduser().resolve()
    if out.exists():
        raise FileExistsError(out)
    if render and not os.environ.get('HELICON_RENDER_BRIEF'):
        raise ValueError('This render must be dispatched through the existing render gate')
    out.mkdir(parents=True, exist_ok=False)
    events = []

    def save(stage, detail):
        events.append({'stage': stage, 'detail': detail})
        (out / 'events.json').write_text(json.dumps(events, indent=2))

    # This is a new transcription. Its heuristic ranking is distinct from the
    # existing source-bound reviewed word alignment and story selection.
    take = transcribe_folder(spec['camera_folder'], spec['model'], out / 'transcription')
    save('transcription', {'backend': take['backend'], 'segments': len(take['segments']),
                           'hooks': take['hooks'], 'fresh': True})
    library = discover(spec['library_roots'], spec['music_query'], 'audio')
    (out / 'library.json').write_text(json.dumps(library, indent=2))
    if not library['results']:
        raise ValueError('No matching shelf audio; no synthetic or paid replacement')
    music = Path(library['results'][0]['path'])
    if digest(music) != spec['music_sha256']:
        raise ValueError('Selected shelf audio differs from the frozen recipe')
    save('library', {'scanned_media': library['scanned_media'], 'selected': str(music),
                     'sha256': digest(music), 'license_scope': spec['music_license_scope']})
    template = Path(spec['template_project']).resolve(strict=True)
    if digest(template) != spec['template_sha256']:
        raise ValueError('Reviewed composition/word animation template changed')
    obj = json.loads(template.read_text())
    # Keep the exact source crop, text words, timing, title, colour and frame guards.
    paths = {Path(a['path']).resolve() for a in obj['assets'].values()}
    if paths != {source}:
        raise ValueError('Template must refer only to the exact frozen take')
    duration = sum(c['out'] - c['in'] for c in obj['clips'])
    obj['assets']['shelf-music'] = {'path': str(music), 'name': music.name}
    obj['audios'] = [{'id': 'shelf-music', 'asset': 'shelf-music', 'in': 0, 'out': duration,
                      'start': 0, 'vol': -24, 'fi': .25, 'fo': 1.5, 'duck': True, 'track': 0}]
    obj['name'] = 'Hermes Studio — one real take through the studio'
    api = editor_api()
    roots = [str(out), str(source.parent), *spec['library_roots']]
    saved = api.save_project(str(out / 'studio.vproj.json'), obj, roots)
    loaded = api.load_project(saved, roots)
    script = {'title': obj['texts'][0]['text'], 'source': str(source),
              'selection': [{k: c[k] for k in ('in', 'out')} for c in obj['clips']],
              'caption_script': [t['text'] for t in obj['texts'][1:]],
              'method': 'Approved source-bound script and word anchors, not invented LLM output',
              'alignment_proof': spec['alignment_proof']}
    aligned = json.loads(Path(spec['alignment_proof']).read_text())
    script['spoken_script'] = ' '.join(w['text'] for w in aligned['words'])
    (out / 'script.json').write_text(json.dumps(script, indent=2))
    save('editable_animation', {'project': saved, 'clips': len(obj['clips']),
                                'word_entry_states': len(obj['texts']) - 1,
                                'music_tracks': len(obj['audios']), 'motion_skill': spec['motion_skill'],
                                'scope': 'Word-anchored blue text entry; no 3D/video-generation claim'})
    result = {'ok': True, 'rendered': False, 'project': saved, 'events': events}
    if render:
        clips = api.sanitize_clips([dict(path=loaded['assets'][c['asset']]['path'],
                                   **{k: c[k] for k in ('in', 'out', 'tf')}) for c in loaded['clips']], roots)
        audios = api.sanitize_audios([dict(path=loaded['assets'][a['asset']]['path'],
                                    **{k: a[k] for k in ('in', 'out', 'start', 'vol', 'fi', 'fo', 'duck', 'track')})
                                     for a in loaded['audios']], roots)
        media = out / 'native-render'
        media.mkdir(exist_ok=False)
        film = api.render_project(clips, media, canvas=loaded['canvas'], bg=loaded['bg'],
                                  texts=loaded['texts'], audios=audios)
        audio = importlib.import_module('studio_native_editor.tools.audio')
        normalized = audio.normalize_loudness({'input': str(film), 'output': str(media / 'short.mp4'),
                                               'target_lufs': -14, 'verify': True})
        if isinstance(normalized, str):
            normalized = json.loads(normalized)
        if normalized.get('ok') is not True:
            raise RuntimeError(normalized)
        save('export', normalized)
        gate = studio_gate(normalized['output'])
        save('measurement', gate)
        # Missing final-file visual evidence deliberately fails the combined gate.
        result.update(rendered=True, video=normalized['output'], gate=gate,
                      review_required=True, events=events)
    (out / 'result.json').write_text(json.dumps(result, indent=2))
    return result
