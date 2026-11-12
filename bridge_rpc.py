"""SSH stdin/stdout queue RPC. Run only under the configured SSH account.

No listening port, shell commands from job data, automatic render, or LLM call.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
from channel import atomic, config, job_path, allowed_file


def _version(root, obj):
    """Tree label of this video and of the video it was made from. A label problem never blocks delivery."""
    try:
        import take_tree
        return take_tree.label(root, obj['id'])
    except Exception:
        return None, None


def rpc(root, payload):
    op = payload['op']
    if op == 'pending':
        return [json.loads(p.read_text()) for p in sorted((root/'jobs').glob('*.json'))
                if json.loads(p.read_text())['state'] == 'queued']
    job = job_path(root, payload['id'])
    obj = json.loads(job.read_text())
    if op == 'deliver':
        # Worker alone invokes this after a successful preview result. Recipient
        # comes from explicit host config, never from queue/model arguments.
        if obj['action'] not in {'prepare','preview','finish','approve','revise'} or obj['state'] != 'completed' or not obj.get('result',{}).get('preview'):
            raise ValueError('Job has no completed checked preview')
        from telegram_delivery import send_preview
        import hashlib
        raw=base64.b64decode(payload['base64'],validate=True)
        expected=obj['result']['preview_sha256']
        if hashlib.sha256(raw).hexdigest()!=expected:
            raise ValueError('Worker preview differs from job receipt')
        folder=root/'deliveries';folder.mkdir(exist_ok=True)
        path=folder/(obj['id']+'.mp4')
        if not path.exists():
            with path.open('xb') as stream:stream.write(raw)
        caption='Your checked Studio preview. Reply with the change you want.'
        name,base=_version(root,obj)
        if obj['result'].get('state')=='revised':
            r=obj['result']
            caption=(name or 'Version '+str(r.get('revision')))+(', made from '+base if base else '')+'. Changed: '+'; '.join(r.get('changed',[]))+('. Could not: '+'; '.join(r.get('skipped',[])) if r.get('skipped') else '')+'. Previous version kept. Reply with more changes.'
            caption=caption[:1000]
        if obj['result'].get('state')=='generated':
            caption=(f"{name}: " if name else '')+f"New version with a generated opening ({obj['result'].get('model')}). Reply with another letter or a new shot idea."
        if obj['result'].get('state')=='finished':
            caption=(f"{name}. " if name else '')+f"Finished: {obj['result'].get('style')}. Reply A, B or C for another style, or tell me what to change. Reply to any older video to start from it instead."
        if obj['result'].get('state')=='existing_reviewed_preview':
            caption='Studio connection test: this is the existing checked Short, unchanged. No new edit was rendered. Reply with a change to continue.'
        delivered=send_preview(path,expected,config(root),folder/(obj['id']+'.json'),caption)
        obj['delivery']=delivered
        if name:
            obj['version']=name  # frozen: the tree keeps the name the creator saw
        atomic(job,obj)
        return delivered
    if op == 'deliver_photo':
        # The take tree image. Stored apart from 'delivery', so a reply to the image never resolves to a video.
        if obj['action']!='tree' or obj['state']!='completed' or obj.get('result',{}).get('state')!='tree_ready':
            raise ValueError('Job has no completed tree image')
        from telegram_delivery import send_photo
        import hashlib
        raw=base64.b64decode(payload['base64'],validate=True)
        expected=obj['result']['image_sha256']
        if hashlib.sha256(raw).hexdigest()!=expected:
            raise ValueError('Worker image differs from job receipt')
        folder=root/'deliveries';folder.mkdir(exist_ok=True)
        path=folder/(obj['id']+'.png')
        if not path.exists():
            with path.open('xb') as stream:stream.write(raw)
        count=sum(1 for n in obj.get('tree',[]) if n.get('kind')=='version')
        caption=f"Take tree: {count} versions of this project. Reply to any video in the chat to start a new version from it. Nothing is overwritten."
        delivered=send_photo(path,expected,config(root),folder/(obj['id']+'.json'),caption)
        obj['photo_delivery']=delivered
        atomic(job,obj)
        return delivered
    if op == 'notify':
        # Progress and wrap-up text for a real job, to the configured owner chat only.
        from telegram_delivery import send_text
        sent=send_text(payload['text'],config(root))
        # Never store the text: a quote message carries the one-time approval code, and this job is agent-readable.
        obj.setdefault('notes',[]).append({'state':sent['state'],'chars':len(str(payload['text'])),'bound':bool(payload.get('bind'))})
        if payload.get('bind') and sent.get('state')=='sent':
            # A reply to this message (e.g. YES to a quote) resolves back to this job.
            obj.setdefault('deliveries',[]).append({'state':'sent','message_id':sent['message_id'],'chat_id':config(root)['delivery_chat_id'],'kind':'text'})
        atomic(job,obj)
        return sent
    if op == 'deliver_option':
        # One style alternative per message, so a reply to it resolves to this job and option.
        if obj['action'] != 'alternatives' or obj['state'] != 'completed':
            raise ValueError('Job has no completed alternatives')
        option=str(payload['option'])
        match=[o for o in obj['result'].get('options',[]) if o['option']==option]
        if len(match)!=1:
            raise ValueError('Unknown option')
        from telegram_delivery import send_preview
        import hashlib
        raw=base64.b64decode(payload['base64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=match[0]['preview_sha256']:
            raise ValueError('Worker option preview differs from job receipt')
        folder=root/'deliveries';folder.mkdir(exist_ok=True)
        path=folder/(obj['id']+'-'+option+'.mp4')
        if not path.exists():
            with path.open('xb') as stream:stream.write(raw)
        caption=f"{option}: {match[0]['label']}. Reply {option} (or A, B, C) to get the full finished short in that style."
        delivered=send_preview(path,match[0]['preview_sha256'],config(root),folder/(obj['id']+'-'+option+'.json'),caption)
        delivered=dict(delivered,option=option)
        obj.setdefault('deliveries',[]).append(delivered)
        atomic(job,obj)
        return delivered
    if op == 'attachment':
        path = allowed_file(obj['source'], config(root)['inbound_roots'])
        import hashlib
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != obj['source_sha256']:
            raise ValueError('Inbound source changed after enqueue')
        return {'name': path.name, 'base64': base64.b64encode(raw).decode()}
    if op == 'finish':
        if obj['state'] != 'queued':
            raise ValueError('A completed job cannot be overwritten')
        obj.update(state=payload['state'], result=payload['result'])
        atomic(job, obj)
        return obj
    raise ValueError('Unsupported RPC')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    a = p.parse_args()
    try:
        print(json.dumps({'ok': True, 'result': rpc(a.root, json.load(sys.stdin))}))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)}))
        raise SystemExit(1)
