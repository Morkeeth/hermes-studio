"""One bounded workstation queue pass, reached via outbound SSH only.

Library discovery and reversible project edits run locally. New media renders
remain held for the configured render gate. Delivery is a separate operation.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
from channel import atomic
from library import discover


def project_state(path):
    raw=Path(path).read_bytes();obj=json.loads(raw)
    return {'native_sha256':hashlib.sha256(raw).hexdigest(),
            'editable_state':{k:obj.get(k) for k in ('canvas','clips','texts','audios')}}


def remote(cfg, payload):
    host = cfg['ssh_host']
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', host) or host.startswith('-'):
        raise ValueError('Invalid configured SSH host')
    # Optional operator-configured host launcher supplies the existing profile
    # environment. Job/model data never chooses executables or secret sources.
    prefix=cfg.get('remote_prefix',[])
    if not isinstance(prefix,list) or not all(isinstance(x,str) for x in prefix):
        raise ValueError('remote_prefix must be an operator-configured argv list')
    command = shlex.join([*prefix,cfg.get('remote_python', 'python3'), cfg['remote_rpc'], '--root', cfg['remote_queue']])
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=12', host, command],
                       input=json.dumps(payload), capture_output=True, text=True, timeout=120)
    reply = json.loads(p.stdout)
    if p.returncode or not reply.get('ok'):
        raise RuntimeError(reply.get('error', 'SSH RPC failed'))
    return reply['result']


def process(cfg, job, fetch):
    root = Path(cfg['workspace']).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if job['action'] == 'library':
        return 'completed', discover(cfg['library_roots'], job['query'])
    if job['action'] in ('alternatives', 'finish'):
        return creative(cfg, job, fetch, root)
    if job['action'] in ('generate', 'approve'):
        return generation(cfg, job, root)
    if job['action'] == 'revise':
        return revise(cfg, job, root, fetch)
    if job['action'] == 'prepare':
        blob = fetch(job['id'])
        raw = base64.b64decode(blob['base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != job['source_sha256']:
            raise ValueError('Transferred attachment hash mismatch')
        folder = root/job['id']
        folder.mkdir(exist_ok=False)
        # Ignore sender filename for local path construction.
        source = folder/'source.mp4'
        source.write_bytes(raw)
        project = {'project_id': job['id'], 'revision': 0, 'source': str(source),
                   'source_sha256': job['source_sha256'], 'native_project': None,
                   'preview': None, 'state': 'needs_reviewed_cut_recipe'}
        for seed in cfg.get('reviewed_projects', []):
            if seed['source_sha256'] == job['source_sha256']:
                project.update(native_project=seed['project'], preview=seed['preview'],
                               preview_sha256=seed['preview_sha256'], state='existing_reviewed_preview')
                project.update(project_state(seed['project']))
                break
        atomic(folder/'revision-0.json', project)
        atomic(folder/'current.json', project)
        return 'completed', project
    identity = str(job.get('project_id', ''))
    if not re.fullmatch(r'[a-f0-9]{32}', identity):
        raise ValueError('Unknown project ID')
    folder = root/identity
    current = json.loads((folder/'current.json').read_text())
    if job['action'] == 'edit':
        # Write immutable successor, preserving source and every older revision.
        if not current.get('native_project'):
            raise ValueError('Project has no reviewed editable cut yet')
        if job.get('base_revision') != current['revision']:
            raise ValueError('Stale reply edit: inspect the current project revision first')
        observed=project_state(current['native_project'])
        if current.get('native_sha256') and observed['native_sha256']!=current['native_sha256']:
            raise ValueError('Native project changed outside this revision; inspect before editing')
        native = json.loads(Path(current['native_project']).read_text())
        if not native.get('audios'):
            raise ValueError('No music track exists to edit')
        gain = job['edit']['music_gain_db']
        for track in native['audios']:
            track['vol'] = gain
        revision = current['revision'] + 1
        path = folder/('edit-%d.vproj.json' % revision)
        with path.open('x') as stream:
            json.dump(native, stream, indent=2)
        successor = dict(current, revision=revision, native_project=str(path),
                         preview=None, state='awaiting_render_gate', request_job=job['id'])
        successor.update(project_state(path))
        atomic(folder/('revision-%d.json' % revision), successor)
        atomic(folder/'current.json', successor)
        return 'awaiting_render_gate', successor
    if job['action'] == 'preview':
        if not current.get('preview'):
            return 'awaiting_render_gate', current
        preview = Path(current['preview']).resolve(strict=True)
        if hashlib.sha256(preview.read_bytes()).hexdigest() != current['preview_sha256']:
            raise ValueError('Reviewed preview changed')
        return 'completed', current
    raise ValueError('Unknown action')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _receive(job, fetch, folder):
    blob = fetch(job['id'])
    raw = base64.b64decode(blob['base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != job['source_sha256']:
        raise ValueError('Transferred attachment hash mismatch')
    source = folder / 'source.mp4'
    source.write_bytes(raw)
    return source


def creative(cfg, job, fetch, root):
    """Fresh footage to style alternatives, or to a finished short in the picked style. Renders locally, now."""
    from connectors import finish as studio_finish, styles as studio_styles
    folder = root / job['id']
    folder.mkdir(exist_ok=False)
    model = cfg.get('whisper_model')
    if job['action'] == 'alternatives':
        source = _receive(job, fetch, folder)
        r = studio_finish.alternatives(source, folder / 'alternatives', job.get('title', ''), model, music_beds=cfg.get('music_beds'), broll=cfg.get('broll'))
        for o in r['options']:
            o['preview_sha256'] = _sha(o['preview'])
        return 'completed', {'state': 'alternatives_ready', 'source': str(source), 'options': r['options'],
                             'question': r['question'], 'project_id': job['id'], 'title': job.get('title') or r.get('title', ''),
                             'transcript': r.get('transcript', ''), 'editor_note': r.get('editor_note')}
    # finish: the source and offered options come from the replied-to parent job, or from a new attachment
    parent = json.loads((root / job['parent'] / 'result.json').read_text()) if job.get('parent') else None
    source = Path(parent['source']) if parent else _receive(job, fetch, folder)
    style = job['style']
    if len(style) == 1:
        options = (parent or {}).get('options') or []
        match = [o for o in options if o['option'] == style.upper()]
        if not match:
            raise ValueError('That letter was not one of the offered alternatives')
        style = match[0]['style']
    studio_styles.get(style)
    title = job.get('title') or (parent or {}).get('title', '')
    bed = (cfg.get('music_beds') or {}).get(style)
    r = studio_finish.finish(source, folder / 'finished', title=title, model=model, style=style,
                             music=bed, music_db=studio_styles.get(style).get('music_db', -20), broll=cfg.get('broll'))
    revision = ((parent or {}).get('revision') or 0) + (1 if parent and parent.get('state') == 'finished' else 0)
    return 'completed', {'state': 'finished', 'source': str(source), 'style': style, 'title': title,
                         'preview': r['output'], 'preview_sha256': _sha(r['output']), 'captions': r['captions'],
                         'recipe': r['recipe'], 'options': (parent or {}).get('options'), 'revision': revision,
                         'project_id': (parent or {}).get('project_id', job['id'])}


def generation(cfg, job, root):
    """Quote first; spend only after the creator's YES; insert the shot as a new version."""
    from connectors import gen_api, finish as studio_finish
    folder = root / job['id']
    folder.mkdir(exist_ok=False)
    parent = json.loads((root / job['parent'] / 'result.json').read_text())
    if job['action'] == 'generate':
        q = gen_api.quote(job['title'], job['query'], cfg.get('monthly_video_cap_usd', 20.0))
        # The one-time code stays on the workstation and in the creator's chat. The gateway, the agent and
        # studio_status only see the quote without it, so the agent cannot approve on the creator's behalf.
        atomic(folder / 'quote-secret.json', q)
        # Only these fields leave the workstation. Nothing derived from the code (no hash, no message) is in them,
        # so the code cannot be recovered offline from what the gateway or the agent can read.
        PUBLIC = ('model', 'provider', 'provider_model', 'prompt', 'cost_usd', 'spent_this_month_usd', 'monthly_cap_usd', 'blockers', 'expires_at')
        public = {k: q[k] for k in PUBLIC if k in q}
        return 'completed', {'state': 'awaiting_approval', 'quote': public, 'quote_message': q['message'],
                             'finished': parent['preview'], 'source': parent['source'],
                             'options': parent.get('options'), 'title': parent.get('title'), 'project_id': parent.get('project_id')}
    secret_path = root / job['parent'] / 'quote-secret.json'
    import fcntl
    with open(secret_path.with_suffix('.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # two approvals of one quote: only the first can mark it used
        q = json.loads(secret_path.read_text())
        if q.get('used'):
            raise PermissionError('this quote was already spent')
        if str(job.get('query', '')).strip().upper() != 'YES ' + q['approval_code']:
            raise PermissionError('approval code does not match this quote')
        atomic(secret_path, dict(q, used=True))  # marked before spending: a crash afterwards never allows a second spend
    frame = folder / 'reference.png'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', '2.5', '-i', parent['finished'], '-frames:v', '1', str(frame)], check=True)
    shot = gen_api.generate(q, job['query'], folder / 'shot', image=frame)
    r = studio_finish.insert_shot(parent['finished'], shot['video'], folder / 'with-shot')
    return 'completed', {'state': 'generated', 'model': q['model'], 'cost_usd': q['cost_usd'], 'shot': shot['video'],
                         'preview': r['output'], 'preview_sha256': _sha(r['output']), 'source': parent['source'],
                         'options': parent.get('options'), 'title': parent.get('title'), 'project_id': parent.get('project_id')}


CAPTION_KEYS = ('box', 'text', 'size', 'words', 'y')


def revise(cfg, job, root, fetch=None):
    """Several changes in one reply, rendered as a new version. Says plainly what it could not do."""
    from connectors import finish as studio_finish, styles as studio_styles
    folder = root / job['id']
    folder.mkdir(exist_ok=False)
    parent = json.loads((root / job['parent'] / 'result.json').read_text())
    recipe = json.loads(Path(parent['recipe']).read_text()) if parent.get('recipe') else {}
    e = job['edit']
    letters = {o['option']: o['style'] for o in (parent.get('options') or [])}
    def pick(v):
        v = str(v).strip()
        return letters.get(v.upper(), v) if len(v) == 1 else v
    base = pick(e['style']) if 'style' in e else parent.get('style', 'hermes-dither')
    st = dict(studio_styles.get(base))
    changed, skipped = [f'style {base}'], []
    if e.get('look_from_source'):
        # recreate a look: the reference clip the creator sent becomes a new style (dither pair, caption box, text)
        ref = _receive(job, fetch, folder)
        frame = folder / 'reference.png'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', '1.0', '-i', str(ref), '-frames:v', '1', str(frame)], check=True)
        look = studio_styles.from_reference(frame, 'your reference')
        st.update({k: look[k] for k in ('intro_dark', 'intro_light', 'box', 'text', 'highlight', 'highlight_text')})
        changed.append('look taken from your reference clip')
    if 'captions_from' in e:
        src = studio_styles.get(pick(e['captions_from']))
        st.update({k: src[k] for k in CAPTION_KEYS}); changed.append(f"captions from {pick(e['captions_from'])}")
    start = float(recipe.get('start_at') or 0) + float(e.get('trim_start') or 0)
    if e.get('trim_start'):
        changed.append(f"opening tightened by {e['trim_start']} s")
    intro = float(recipe.get('intro_seconds') or 1.6)
    if e.get('punchier'):
        intro = max(0.6, intro - 0.6); changed.append(f'shorter, punchier intro ({intro:.1f} s)')
    title = str(e.get('title') or parent.get('title') or '')
    if e.get('title'):
        changed.append('new title')
    music = recipe.get('music') or (cfg.get('music_beds') or {}).get(base)
    music_db = float(recipe.get('music_db') if recipe.get('music') else st.get('music_db', -20))
    if e.get('quieter_music'):
        if music:
            music_db -= 6; changed.append(f'music bed 6 dB quieter ({music_db:.0f} dB)')
        else:
            layout = studio_finish.audio_layout(parent['source'])
            skipped.append('quieter music: ' + (layout['note'] if layout['streams'] == 1 else 'no separate music track in this source'))
    r = studio_finish.finish(parent['source'], folder / 'revised', title=title, words=recipe.get('words'), style=st,
                             intro_seconds=intro, start_at=start, music=music, music_db=music_db, broll=(recipe.get('broll') if str(recipe.get('broll') or '').startswith('/') else None) or cfg.get('broll'))
    return 'completed', {'state': 'revised', 'style': base, 'changed': changed, 'skipped': skipped, 'title': title,
                         'preview': r['output'], 'preview_sha256': _sha(r['output']), 'recipe': r['recipe'], 'captions': r['captions'],
                         'source': parent['source'], 'options': parent.get('options'),
                         'revision': int(parent.get('revision') or 0) + 1, 'project_id': parent.get('project_id')}


def say(cfg, job, text, bind=False):
    try:
        remote(cfg, {'op': 'notify', 'id': job['id'], 'text': text, 'bind': bind})
    except Exception as e:
        print(json.dumps({'id': job['id'], 'notify_failed': str(e)[:200]}))


def wrapup(result):
    lines = [f"Your three previews are ready. Title I picked from what you said: \"{result.get('title') or 'none'}\"."]
    for o in result['options']:
        lines.append(f"{o['option']}: {o['label']}")
    note = result.get('editor_note') or {}
    letter = next((o['option'] for o in result['options'] if o['style'] == note.get('pick')), None)
    if letter:
        lines.append(f"My pick: {letter}. {note.get('why', '')}")
    lines.append('Reply to the one you like with its letter for the full finished short, or with changes. Rendered and sent; I cannot see whether it played on your phone.')
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--deliver-ready', action='store_true', help='Explicitly send checked preview jobs to configured allowed Telegram recipient, once')
    a = p.parse_args()
    cfg = json.loads(a.config.read_text())
    root = Path(cfg['workspace']).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    # File lock prevents two workers from processing one queue simultaneously.
    import fcntl
    with (root/'.worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for job in remote(cfg, {'op': 'pending'}):
            if a.deliver_ready and job['action'] == 'approve':
                say(cfg, job, 'Approved. Generating the shot now; this can take a few minutes.')
            if a.deliver_ready and job['action'] == 'revise':
                say(cfg, job, 'Working on your changes. The previous version stays as it is.')
            if a.deliver_ready and job['action'] in ('alternatives', 'finish'):
                say(cfg, job, 'Got it. Transcribing and designing your edit now. About a minute.' if job['action'] == 'alternatives'
                    else f"Rendering the full short in style {job.get('style')}. About a minute.")
            try:
                state, result = process(cfg, job, lambda identity: remote(cfg, {'op': 'attachment', 'id': identity}))
            except Exception as e:
                state, result = 'failed', {'error': str(e)}
            quote_message = result.pop('quote_message', None) if isinstance(result, dict) else None
            if job['action'] in ('alternatives', 'finish', 'generate', 'approve', 'revise') and state == 'completed':
                atomic(root / job['id'] / 'result.json', result)
            remote(cfg, {'op': 'finish', 'id': job['id'], 'state': state, 'result': result})
            print(json.dumps({'id': job['id'], 'state': state, 'result': result}))
            if a.deliver_ready and state == 'failed' and job['action'] in ('alternatives', 'finish', 'generate', 'approve', 'revise'):
                say(cfg, job, 'That did not work: ' + str(result.get('error', ''))[:200] + ' Nothing earlier was overwritten.')
            if a.deliver_ready and state=='completed' and result.get('state')=='awaiting_approval':
                say(cfg, job, quote_message, bind=True)
                continue
            if a.deliver_ready and state=='completed' and result.get('options') and result.get('state')=='alternatives_ready':
                for o in result['options']:
                    delivered=remote(cfg,{'op':'deliver_option','id':job['id'],'option':o['option'],
                        'base64':base64.b64encode(Path(o['preview']).read_bytes()).decode()})
                    print(json.dumps({'id':job['id'],'option':o['option'],'delivery':delivered}))
                say(cfg, job, wrapup(result))
                continue
            if a.deliver_ready and state=='completed' and result.get('preview'):
                delivered=remote(cfg,{'op':'deliver','id':job['id'],
                    'base64':base64.b64encode(Path(result['preview']).read_bytes()).decode()})
                atomic(root/(job['id']+'-delivery.json'),delivered)
                print(json.dumps({'id':job['id'],'delivery':delivered}))


if __name__ == '__main__':
    main()
