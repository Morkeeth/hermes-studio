"""SSH stdin/stdout queue RPC. Run only under the configured SSH account.

No listening port, shell commands from job data, automatic render, or LLM call.
"""
import argparse
import base64
import json
from pathlib import Path
import sys
from channel import atomic, config, job_path, allowed_file


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
        if obj['action'] not in {'prepare','preview'} or obj['state'] != 'completed' or not obj.get('result',{}).get('preview'):
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
        if obj['result'].get('state')=='existing_reviewed_preview':
            caption='Studio connection test: this is the existing checked Short, unchanged. No new edit was rendered. Reply with a change to continue.'
        delivered=send_preview(path,expected,config(root),folder/(obj['id']+'.json'),caption)
        obj['delivery']=delivered
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
