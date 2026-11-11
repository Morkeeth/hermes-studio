"""Explicit one-attempt Telegram preview delivery; token remains on gateway host.

No polling, model call, publishing, automatic retry or inferred recipient.
An uncertain HTTP result remains held to avoid duplicate sends.
"""
import hashlib
import json
from pathlib import Path
import urllib.request
import uuid


def secret_value():
    from agent.secret_scope import get_secret
    return get_secret('TELEGRAM_BOT_TOKEN')


def send_preview(file, expected_sha256, cfg, receipt, caption='Your Studio preview. Reply with the change you want.'):
    path = Path(file).resolve(strict=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Preview differs from checked artifact')
    if len(raw) > 48 * 1024 * 1024:
        raise ValueError('Preview exceeds configured bounded upload limit')
    chat = str(cfg['delivery_chat_id'])
    if chat not in [str(c) for c in cfg['allowed_chat_ids']]:
        raise ValueError('Delivery target is not explicitly allowed')
    token = secret_value()
    if not token:
        raise ValueError('Gateway bot token unavailable')
    receipt = Path(receipt)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    if receipt.exists():
        previous=json.loads(receipt.read_text())
        if previous.get('state')=='sent' and previous.get('sha256')==expected_sha256 and str(previous.get('chat_id'))==chat:
            return previous
        raise FileExistsError('Prior delivery is uncertain or differs; inspect before retry')
    with receipt.open('x') as stream:
        json.dump({'state':'started_unknown_until_confirmed','sha256':expected_sha256}, stream)
    boundary = 'studio-' + uuid.uuid4().hex
    parts=[]
    for name,value in [('chat_id',chat),('caption',caption),('supports_streaming','true')]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="video"; filename="studio-preview.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode()+raw+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendVideo',data=b''.join(parts),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
    try:
        with urllib.request.urlopen(req,timeout=90) as response:
            result=json.load(response)
        if result.get('ok') is not True:
            raise RuntimeError('Telegram did not confirm delivery')
        msg=result['result']
        safe={'state':'sent','sha256':expected_sha256,'message_id':msg['message_id'],
              'chat_id':msg['chat']['id'],'video':{k:msg.get('video',{}).get(k) for k in ('duration','width','height','file_size')}}
        receipt.write_text(json.dumps(safe,indent=2))
        return safe
    except Exception as exc:
        # Exception URLs can contain the bot token. Never return the exception text.
        raise RuntimeError('Delivery unconfirmed ('+type(exc).__name__+'); inspect receipt before any retry') from None


def send_text(text, cfg):
    """One plain status message to the configured owner chat. No retries, no other recipients."""
    chat = str(cfg['delivery_chat_id'])
    if chat not in [str(c) for c in cfg['allowed_chat_ids']]:
        raise ValueError('Delivery target is not explicitly allowed')
    token = secret_value()
    if not token:
        raise ValueError('Gateway bot token unavailable')
    body = json.dumps({'chat_id': chat, 'text': str(text)[:1500]}).encode()
    req = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.load(response)
        return {'state': 'sent' if result.get('ok') else 'unconfirmed', 'message_id': result.get('result', {}).get('message_id')}
    except Exception as exc:
        raise RuntimeError('Status message unconfirmed (' + type(exc).__name__ + ')') from None
