"""Generation models (Higgsfield first). Dry run only.

No network call exists in this module. A paid call needs the owner's explicit
yes and a monthly video budget; until a provider is verified and wired, every
request returns the plan it would send and the reasons it was not sent.
"""
import os

# Bring your own provider: each user connects their own account with their own key.
# Keys are read from the environment only; Studio never stores or logs them.
PROVIDERS = {
    'higgsfield': {'env_key': 'HIGGSFIELD_API_KEY', 'kinds': ['video', 'image'],
                   'docs': 'unverified: confirm current API or MCP server before wiring'},
    'fal': {'env_key': 'FAL_KEY', 'kinds': ['video', 'image'], 'docs': 'live adapter: connectors/gen_api.py (quote, YES, generate)'},
    'replicate': {'env_key': 'REPLICATE_API_TOKEN', 'kinds': ['video', 'image'], 'docs': 'live adapter: connectors/gen_api.py (quote, YES, generate)'},
    'elevenlabs': {'env_key': 'ELEVENLABS_API_KEY', 'kinds': ['voice', 'sound_effect', 'music'],
                   'docs': 'unverified: confirm current API before wiring'},
}


def connected():
    """Which of the user's own providers have a key present. Never returns the key."""
    return {name: bool(os.environ.get(p['env_key'])) for name, p in PROVIDERS.items()}


def plan(provider, prompt, kind='video', seconds=5, allow_spend=False, monthly_cap_usd=20.0):
    if provider not in PROVIDERS:
        raise ValueError(f'unknown provider {provider}; known: {sorted(PROVIDERS)}')
    p = PROVIDERS[provider]
    if kind not in p['kinds']:
        raise ValueError(f'{provider} does not list kind {kind}')
    if not prompt.strip():
        raise ValueError('empty prompt')
    blocked = ['dry run: no network call is wired for any generation provider']
    if not allow_spend:
        blocked.append('allow_spend is false: paid generation needs the owner\'s explicit yes')
    if not os.environ.get(p['env_key']):
        blocked.append(f'{p["env_key"]} is not set')
    return {'sent': False, 'provider': provider, 'request': {'kind': kind, 'prompt': prompt, 'seconds': seconds},
            'monthly_cap_usd': monthly_cap_usd, 'blocked_because': blocked, 'docs': p['docs']}
