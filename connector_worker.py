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
        r = studio_finish.alternatives(source, folder / 'alternatives', job.get('title', ''), model)
        for o in r['options']:
            o['preview_sha256'] = _sha(o['preview'])
        return 'completed', {'state': 'alternatives_ready', 'source': str(source), 'options': r['options'],
                             'question': r['question'], 'project_id': job['id'], 'title': job.get('title', '')}
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
    r = studio_finish.finish(source, folder / 'finished', title=title, model=model, style=style)
    revision = ((parent or {}).get('revision') or 0) + (1 if parent and parent.get('state') == 'finished' else 0)
    return 'completed', {'state': 'finished', 'source': str(source), 'style': style, 'title': title,
                         'preview': r['output'], 'preview_sha256': _sha(r['output']), 'captions': r['captions'],
                         'recipe': r['recipe'], 'options': (parent or {}).get('options'), 'revision': revision,
                         'project_id': (parent or {}).get('project_id', job['id'])}


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
            try:
                state, result = process(cfg, job, lambda identity: remote(cfg, {'op': 'attachment', 'id': identity}))
            except Exception as e:
                state, result = 'failed', {'error': str(e)}
            if job['action'] in ('alternatives', 'finish') and state == 'completed':
                atomic(root / job['id'] / 'result.json', result)
            remote(cfg, {'op': 'finish', 'id': job['id'], 'state': state, 'result': result})
            print(json.dumps({'id': job['id'], 'state': state, 'result': result}))
            if a.deliver_ready and state=='completed' and result.get('options') and result.get('state')=='alternatives_ready':
                for o in result['options']:
                    delivered=remote(cfg,{'op':'deliver_option','id':job['id'],'option':o['option'],
                        'base64':base64.b64encode(Path(o['preview']).read_bytes()).decode()})
                    print(json.dumps({'id':job['id'],'option':o['option'],'delivery':delivered}))
                continue
            if a.deliver_ready and state=='completed' and result.get('preview'):
                delivered=remote(cfg,{'op':'deliver','id':job['id'],
                    'base64':base64.b64encode(Path(result['preview']).read_bytes()).decode()})
                atomic(root/(job['id']+'-delivery.json'),delivered)
                print(json.dumps({'id':job['id'],'delivery':delivered}))


if __name__ == '__main__':
    main()
