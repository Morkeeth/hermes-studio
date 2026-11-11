"""Bring your own video model: one adapter, many models, via fal or Replicate.

Flow, always in this order:
  1. quote()     -> the model, the prompt, the cost from YOUR models.json (or "unknown"), a one-time approval code
  2. the creator replies YES in the chat
  3. generate()  -> refuses without that approval and inside the monthly cap; submits, waits, downloads one MP4
Keys are read from the environment on the workstation only (FAL_KEY, REPLICATE_API_TOKEN). They never enter
a job, a log or a chat. Studio does not guess prices: you write them in models.json from the provider's pricing page.

API shapes verified against the providers' docs on 11 Nov 2026:
  fal:        POST https://queue.fal.run/{model}  (Authorization: Key ...), poll status_url, GET response_url
  replicate:  POST https://api.replicate.com/v1/models/{owner}/{name}/predictions  (Authorization: Bearer ..., Prefer: wait)
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.request
from datetime import datetime
from pathlib import Path

CONFIG = Path(os.environ.get('HERMES_STUDIO_CONFIG', Path.home() / '.config' / 'hermes-studio')).expanduser()
PROVIDERS = {'fal': 'FAL_KEY', 'replicate': 'REPLICATE_API_TOKEN'}


def _models():
    """Your own model list: {"name": {"provider": "fal", "model": "<id from the provider>", "cost_usd": 0.5,
    "seconds": 5, "image_field": "image_url"}}. Nothing is assumed; an unknown cost stays unknown."""
    p = CONFIG / 'models.json'
    return json.loads(p.read_text()) if p.is_file() else {}


def _ledger_path():
    return CONFIG / 'spend.json'


def spent_this_month():
    p = _ledger_path()
    month = datetime.now().strftime('%Y-%m')
    if not p.is_file():
        return 0.0
    return sum(e['cost_usd'] or 0 for e in json.loads(p.read_text()) if e['month'] == month)


def _record(entry):
    p = _ledger_path(); p.parent.mkdir(parents=True, exist_ok=True)
    rows = json.loads(p.read_text()) if p.is_file() else []
    rows.append(entry); p.write_text(json.dumps(rows, indent=1))


def quote(name, prompt, monthly_cap_usd=20.0):
    models = _models()
    if name not in models:
        raise ValueError(f'unknown model {name}; add it to {CONFIG / "models.json"} (known: {sorted(models)})')
    m = models[name]
    if m['provider'] not in PROVIDERS:
        raise ValueError(f"provider must be one of {sorted(PROVIDERS)}")
    if not prompt.strip():
        raise ValueError('empty prompt')
    cost = m.get('cost_usd')
    spent = spent_this_month()
    code = secrets.token_hex(3).upper()
    blockers = []
    if not os.environ.get(PROVIDERS[m['provider']]):
        blockers.append(f"{PROVIDERS[m['provider']]} is not set on the workstation")
    if cost is not None and spent + cost > monthly_cap_usd:
        blockers.append(f'would exceed the monthly cap: ${spent:.2f} spent + ${cost:.2f} > ${monthly_cap_usd:.2f}')
    q = {'model': name, 'provider': m['provider'], 'provider_model': m['model'], 'prompt': prompt,
         'cost_usd': cost, 'spent_this_month_usd': round(spent, 2), 'monthly_cap_usd': monthly_cap_usd,
         'approval_code': code, 'approval_hash': hashlib.sha256((code + prompt + name).encode()).hexdigest(),
         'blockers': blockers}
    q['message'] = (f"Generate with {name} ({m['provider']}): " + (f'about ${cost:.2f}' if cost is not None else 'cost UNKNOWN, check the provider pricing page')
                    + f". Spent this month ${spent:.2f} of ${monthly_cap_usd:.2f}. Reply YES to spend."
                    + (' Blocked: ' + '; '.join(blockers) if blockers else ''))
    return q


def _http(url, headers, body=None, method=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method or ('POST' if body is not None else 'GET'))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'provider returned HTTP {e.code}') from None
    except Exception as e:
        raise RuntimeError(f'provider call failed ({type(e).__name__})') from None


def _find_video_url(obj):
    """Model outputs differ; take the first https URL that looks like a video."""
    if isinstance(obj, str):
        return obj if obj.startswith('https://') and any(x in obj.lower() for x in ('.mp4', '.mov', '.webm', 'video')) else None
    if isinstance(obj, dict):
        for k in ('video', 'url', 'output', 'videos'):
            if k in obj:
                u = _find_video_url(obj[k])
                if u:
                    return u
        for v in obj.values():
            u = _find_video_url(v)
            if u:
                return u
    if isinstance(obj, list):
        for v in obj:
            u = _find_video_url(v)
            if u:
                return u
    return None


def _run_fal(model, inputs, key, poll=4.0, limit=600):
    h = {'Authorization': 'Key ' + key, 'Content-Type': 'application/json'}
    sub = _http(f'https://queue.fal.run/{model}', h, inputs)
    t0 = time.time()
    while time.time() - t0 < limit:
        st = _http(sub['status_url'], h)
        if st.get('status') == 'COMPLETED':
            if st.get('error'):
                raise RuntimeError('fal reported an error: ' + str(st.get('error_type') or 'unknown')[:80])
            return _http(sub['response_url'], h)
        time.sleep(poll)
    raise TimeoutError('fal job did not finish in time; nothing was inserted')


def _run_replicate(model, inputs, key, poll=4.0, limit=600):
    h = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Prefer': 'wait=60'}
    owner_name = model.split(':')[0]
    pred = _http(f'https://api.replicate.com/v1/models/{owner_name}/predictions', h, {'input': inputs}, timeout=90)
    t0 = time.time()
    while pred.get('status') not in ('succeeded', 'failed', 'canceled'):
        if time.time() - t0 > limit:
            raise TimeoutError('replicate prediction did not finish in time; nothing was inserted')
        time.sleep(poll)
        pred = _http(pred['urls']['get'], {'Authorization': 'Bearer ' + key})
    if pred['status'] != 'succeeded':
        raise RuntimeError('replicate prediction ' + pred['status'])
    return pred.get('output')


def generate(q, approval, out_dir, image=None):
    """Spend only with the exact approval code from the quote. Downloads one MP4 into out_dir."""
    if q['blockers']:
        raise PermissionError('blocked: ' + '; '.join(q['blockers']))
    if str(approval).strip().upper() not in ('YES', q['approval_code']):
        raise PermissionError('not approved: reply YES to the quote first')
    if hashlib.sha256((q['approval_code'] + q['prompt'] + q['model']).encode()).hexdigest() != q['approval_hash']:
        raise PermissionError('quote changed after approval')
    m = _models()[q['model']]
    key = os.environ[PROVIDERS[m['provider']]]
    inputs = {'prompt': q['prompt'], **m.get('extra', {})}
    if image:
        data = base64.b64encode(Path(image).read_bytes()).decode()
        inputs[m.get('image_field', 'image_url')] = 'data:image/png;base64,' + data
    _record({'month': datetime.now().strftime('%Y-%m'), 'at': datetime.now().isoformat(timespec='seconds'),
             'model': q['model'], 'cost_usd': q['cost_usd'], 'state': 'submitted'})
    out = (_run_fal if m['provider'] == 'fal' else _run_replicate)(m['model'], inputs, key)
    url = _find_video_url(out)
    if not url:
        raise RuntimeError('the model returned no video URL')
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / 'generated.mp4'
    if dest.exists():
        raise FileExistsError('a generated shot already exists here; use a new directory')
    with urllib.request.urlopen(url, timeout=120) as r, dest.open('xb') as f:
        f.write(r.read())
    return {'ok': True, 'video': str(dest), 'model': q['model'], 'cost_usd': q['cost_usd']}
