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


def remote(cfg, payload):
    host = cfg['ssh_host']
    if not re.fullmatch(r'[A-Za-z0-9_.@-]+', host) or host.startswith('-'):
        raise ValueError('Invalid configured SSH host')
    command = shlex.join([cfg.get('remote_python', 'python3'), cfg['remote_rpc'], '--root', cfg['remote_queue']])
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
            remote(cfg, {'op': 'finish', 'id': job['id'], 'state': state, 'result': result})
            print(json.dumps({'id': job['id'], 'state': state, 'result': result}))
            if a.deliver_ready and state=='completed' and result.get('preview'):
                delivered=remote(cfg,{'op':'deliver','id':job['id'],
                    'base64':base64.b64encode(Path(result['preview']).read_bytes()).decode()})
                atomic(root/(job['id']+'-delivery.json'),delivered)
                print(json.dumps({'id':job['id'],'delivery':delivered}))


if __name__ == '__main__':
    main()
