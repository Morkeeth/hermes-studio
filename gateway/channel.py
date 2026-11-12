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


EDIT_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'music_gain_db': {'type': 'number', 'minimum': -60, 'maximum': 0},
    'style': {'type': 'string'}, 'captions_from': {'type': 'string'},
    'trim_start': {'type': 'number', 'minimum': 0, 'maximum': 30}, 'punchier': {'type': 'boolean'},
    'title': {'type': 'string'}, 'quieter_music': {'type': 'boolean'}, 'look_from_source': {'type': 'boolean'}}}
REVISE_KEYS = set(EDIT_SCHEMA['properties']) - {'music_gain_db'}


def _take_tree():
    try:
        from . import take_tree
    except ImportError:
        import take_tree
    return take_tree


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
    if action not in {'library', 'prepare', 'edit', 'preview', 'alternatives', 'finish', 'generate', 'approve', 'revise', 'tree'}:
        raise ValueError('Choose library, prepare, edit, preview, alternatives, finish, generate, approve, revise or tree')
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
    if action == 'revise':
        if not parent or (parent.get('result') or {}).get('state') not in ('finished', 'generated', 'revised'):
            raise ValueError('revise needs a reply to a finished short')
        allowed = REVISE_KEYS
        if not edit or set(edit) - allowed:
            raise ValueError('revise edit can combine: ' + ', '.join(sorted(allowed)))
        if edit.get('look_from_source') and not args.get('source'):
            raise ValueError('look_from_source needs source=<the reference video the creator sent>')
        if 'trim_start' in edit and not (type(edit['trim_start']) in (int, float) and 0 <= edit['trim_start'] <= 30):
            raise ValueError('trim_start is seconds to cut from the opening, 0 to 30')
    if action == 'edit':
        if set(edit) != {'music_gain_db'} or type(edit['music_gain_db']) not in (int, float) or not -60 <= edit['music_gain_db'] <= 0:
            raise ValueError('This connector currently supports explicit music_gain_db edits only (-60 to 0)')
    style = None
    if action == 'finish':
        style = str(args.get('style', '')).strip()
        if not re.fullmatch(r'[A-Da-d]|[a-z][a-z-]{2,30}', style):
            raise ValueError('finish needs style: a letter A-D from the offered alternatives, or a style name')
        if not parent and not args.get('source'):
            raise ValueError('finish needs the replied-to alternatives or finished video, or a source attachment')
    if action == 'generate':
        if not parent or (parent.get('result') or {}).get('state') != 'finished':
            raise ValueError('generate needs a reply to a finished short')
        if not str(args.get('query', '')).strip() or not str(args.get('title', '')).strip():
            raise ValueError('generate needs query=<prompt for the shot> and title=<model name from models.json>')
    if action == 'approve':
        if not parent or (parent.get('result') or {}).get('state') != 'awaiting_approval':
            raise ValueError('approve needs a reply to a generation quote')
        if not re.fullmatch(r'YES [0-9A-F]{6}', str(args.get('query', '')).strip().upper()):
            raise ValueError("approve needs the creator's exact reply: YES plus the six-character code from the quote. Never supply it yourself.")
    tree = None
    if action == 'tree':
        # The tree is drawn from these records only: whitelisted fields, never a query or an approval code.
        tree = _take_tree().snapshot(root, parent['id'] if parent else None)
        if not tree:
            raise ValueError('No delivered video yet. Send a clip first.')
    source = None
    if args.get('source'):
        source = allowed_file(args['source'], cfg['inbound_roots'])
    if action in ('prepare', 'alternatives') and not source:
        raise ValueError('This action needs the actual downloaded video attachment path')
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
           'query': str(args.get('query', ''))[:400], 'edit': edit,
           'style': style, 'title': str(args.get('title', ''))[:80],
           'parent': parent['id'] if parent else None,
           'base_revision': parent.get('result',{}).get('revision') if parent else None,
           'project_id': parent.get('result', {}).get('project_id') if parent else args.get('project_id'),
           'state': 'queued', 'created_at': time.time(), 'result': None}
    if tree is not None:
        obj['tree'] = tree
    tmp = path.with_name('.request-' + uuid.uuid4().hex)
    tmp.write_text(json.dumps(obj, indent=2))
    try:
        os.link(tmp, path)  # atomic publish without replacing an existing request
    except FileExistsError:
        old = json.loads(path.read_text())
        fields = ('action', 'source', 'source_sha256', 'query', 'edit', 'parent', 'project_id', 'base_revision', 'style')
        if any(old.get(k) != obj.get(k) for k in fields):
            raise ValueError('request_key already belongs to a different request')
        return old
    finally:
        tmp.unlink(missing_ok=True)
    return obj


EXPLAIN = {
    'queued': 'Waiting for the workstation worker to pick this up. Nothing has rendered yet.',
    'awaiting_render_gate': 'The change is saved as a new project version, but no new video exists. A render has to be authorized and run on the workstation. For a finished video now, ask for a style instead (studio_request action finish).',
    'needs_reviewed_cut_recipe': 'This footage has no reviewed recipe for the native editor. To get a finished video now, ask for styles: studio_request action alternatives renders A, B and C from this footage.',
    'alternatives_ready': 'Three style previews were rendered. Reply to one with its letter to get the full finished short.',
    'finished': 'The finished short is rendered. Reply with another letter for a re-cut, or ask for a generated opening shot.',
    'awaiting_approval': 'A generated shot was quoted. Nothing is spent until the creator replies, in the owner chat, YES plus the six-character code shown in the quote (for example YES 7F3A2C). The code proves the reply came from someone who saw that quote in the owner chat; it does not prove which person typed it.',
    'generated': 'The generated shot was added to the start of the short as a new version.',
    'revised': 'The revision is rendered as a new version; the previous version is kept. The message lists what changed and what could not.',
    'tree_ready': 'The take tree is drawn: every version of this project in one image. Reply to any video in the chat to start a new version from it.',
    'failed': 'This step failed; the error says why. Nothing was overwritten.',
}


def explain(job):
    """One plain sentence for the creator: what happened, what is blocking, what to do next."""
    state = (job.get('result') or {}).get('state') or job.get('state')
    text = EXPLAIN.get(state) or EXPLAIN.get(job.get('state'), '')
    if job.get('state') == 'failed' and (job.get('result') or {}).get('error'):
        text += ' Error: ' + str(job['result']['error'])[:200]
    return dict(job, explain=text)


def status(identity=None, root=None, reply_to_message_id=None, source=None):
    root=Path(root) if root else queue_root()
    if reply_to_message_id is not None or source:
        if reply_to_message_id is not None and (type(reply_to_message_id) is not int or reply_to_message_id<1):
            raise ValueError('Supply the actual positive replied-to Telegram message ID')
        checksum=hashlib.sha256(allowed_file(source,config(root)['inbound_roots']).read_bytes()).hexdigest() if source else None
        chat=str(config(root)['delivery_chat_id']);matches=[]
        for path in (root/'jobs').glob('*.json'):
            job=json.loads(path.read_text())
            for delivery in [job.get('delivery') or {}]+list(job.get('deliveries') or []):
                bound=(reply_to_message_id is None or delivery.get('message_id')==reply_to_message_id) and (checksum is None or delivery.get('sha256')==checksum)
                if delivery.get('state')=='sent' and bound and str(delivery.get('chat_id'))==chat:
                    matches.append(job); break
        if len(matches)!=1:
            raise ValueError('No unique delivered project matches this reply')
        if identity and matches[0]['id']!=identity:
            raise ValueError('Job does not match reply target')
        return explain(matches[0])
    if not identity:raise ValueError('Supply job, actual reply_to_message_id, or cached replied-video source')
    return explain(json.loads(job_path(root, identity).read_text()))
