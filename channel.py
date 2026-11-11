"""Conversation job queue. No render, network listener, credential or model call.

The workstation pulls jobs over SSH. A separately configured delivery command
returns checked artifacts; enqueuing a request never means it was completed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid


def atomic(path, obj):
    path = Path(path)
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def queue_root():
    from hermes_constants import get_hermes_home
    root = Path(get_hermes_home()) / 'studio-channel'
    root.mkdir(parents=True, exist_ok=True)
    return root


def config(root):
    return json.loads((Path(root) / 'config.json').read_text())


def job_path(root, job):
    if not re.fullmatch(r'[a-f0-9]{32}', str(job)):
        raise ValueError('Invalid job ID')
    return Path(root) / 'jobs' / (job + '.json')


def allowed_file(value, roots):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm'}:
        raise ValueError('Supply a real video attachment')
    if not any(path.is_relative_to(Path(r).expanduser().resolve(strict=True)) for r in roots):
        raise ValueError('Attachment is outside configured inbound media roots')
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Attachment exceeds this connector limit; use a provisioned local asset')
    return path


def request(args, root=None):
    root = Path(root) if root else queue_root()
    cfg = config(root)
    action = args['action']
    if action not in {'library', 'prepare', 'edit', 'preview'}:
        raise ValueError('Choose library, prepare, edit or preview')
    parent = None
    if args.get('parent'):
        parent = json.loads(job_path(root, args['parent']).read_text())
    if args.get('reply_to_message_id') is not None:
        replied=status(None,root,args['reply_to_message_id'])
        if parent and parent['id']!=replied['id']:
            raise ValueError('Parent does not match the actual delivered reply target')
        parent=replied
    if action=='edit' and not parent and args.get('source'):
        parent=status(None,root,source=args['source'])
    if action == 'edit' and (not parent or not parent.get('result', {}).get('project_id')):
        raise ValueError('Reply edits need a completed parent job with a real project')
    edit = args.get('edit', {})
    if action == 'edit':
        if set(edit) != {'music_gain_db'} or type(edit['music_gain_db']) not in (int, float) or not -60 <= edit['music_gain_db'] <= 0:
            raise ValueError('This connector currently supports explicit music_gain_db edits only (-60 to 0)')
    source = None
    if args.get('source'):
        source = allowed_file(args['source'], cfg['inbound_roots'])
    if action == 'prepare' and not source:
        raise ValueError('Prepare needs the actual downloaded video attachment path')
    key = str(args.get('request_key', ''))
    if not 1 <= len(key) <= 150:
        raise ValueError('Supply a stable request_key, preferably channel:chat:message:operation')
    # Atomic creation by deterministic request ID makes delivery retries reuse a job.
    identity = hashlib.sha256(key.encode()).hexdigest()[:32]
    path = job_path(root, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    obj = {'id': identity, 'request_key': key, 'action': action,
           'source': str(source) if source else None,
           'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest() if source else None,
           'query': str(args.get('query', ''))[:150], 'edit': edit,
           'parent': parent['id'] if parent else None,
           'base_revision': parent.get('result',{}).get('revision') if parent else None,
           'project_id': parent.get('result', {}).get('project_id') if parent else args.get('project_id'),
           'state': 'queued', 'created_at': time.time(), 'result': None}
    tmp = path.with_name('.request-' + uuid.uuid4().hex)
    tmp.write_text(json.dumps(obj, indent=2))
    try:
        os.link(tmp, path)  # atomic publish without replacing an existing request
    except FileExistsError:
        old = json.loads(path.read_text())
        fields = ('action', 'source', 'source_sha256', 'query', 'edit', 'parent', 'project_id', 'base_revision')
        if any(old.get(k) != obj.get(k) for k in fields):
            raise ValueError('request_key already belongs to a different request')
        return old
    finally:
        tmp.unlink(missing_ok=True)
    return obj


def status(identity=None, root=None, reply_to_message_id=None, source=None):
    root=Path(root) if root else queue_root()
    if reply_to_message_id is not None or source:
        if reply_to_message_id is not None and (type(reply_to_message_id) is not int or reply_to_message_id<1):
            raise ValueError('Supply the actual positive replied-to Telegram message ID')
        checksum=hashlib.sha256(allowed_file(source,config(root)['inbound_roots']).read_bytes()).hexdigest() if source else None
        chat=str(config(root)['delivery_chat_id']);matches=[]
        for path in (root/'jobs').glob('*.json'):
            job=json.loads(path.read_text());delivery=job.get('delivery',{})
            bound=(reply_to_message_id is None or delivery.get('message_id')==reply_to_message_id) and (checksum is None or delivery.get('sha256')==checksum)
            if delivery.get('state')=='sent' and bound and str(delivery.get('chat_id'))==chat:
                matches.append(job)
        if len(matches)!=1:
            raise ValueError('No unique delivered project matches this reply')
        if identity and matches[0]['id']!=identity:
            raise ValueError('Job does not match reply target')
        return matches[0]
    if not identity:raise ValueError('Supply job, actual reply_to_message_id, or cached replied-video source')
    return json.loads(job_path(root, identity).read_text())
