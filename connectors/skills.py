"""The skills Studio conducts. Curated from the ecosystem; stars and licences read from the
GitHub API on 11 Nov 2026. Studio links and recommends; it does not vendor other people's code.

status: 'in studio'  = Studio already does this itself
        'recommend'  = install alongside Studio; Hermes suggests it when the job fits
        'next'       = planned adapter
"""

SKILLS = [
    {'name': 'HyperFrames', 'repo': 'heygen-com/hyperframes', 'stars': 82818, 'license': 'Apache-2.0',
     'does': 'write HTML, render video; built for agents', 'jobs': ['animation', 'motion', 'title', 'explainer', 'recreate'], 'status': 'next'},
    {'name': 'OpenMontage', 'repo': 'OpenMontageInc/OpenMontage', 'stars': 79672, 'license': 'AGPL-3.0',
     'does': 'agentic video production pipelines', 'jobs': ['pipeline', 'montage', 'trailer'], 'status': 'recommend'},
    {'name': 'video-use', 'repo': 'browser-use/video-use', 'stars': 30912, 'license': 'MIT',
     'does': 'edit videos with coding agents', 'jobs': ['edit', 'cut', 'trim'], 'status': 'recommend'},
    {'name': 'Remotion skills', 'repo': 'remotion-dev/skills', 'stars': 6860, 'license': 'unlisted',
     'does': 'official Remotion agent skills for programmatic video', 'jobs': ['animation', 'motion', 'captions', 'recreate'], 'status': 'next'},
    {'name': 'ffmpeg-skill', 'repo': 'kajisho5/ffmpeg-skill', 'stars': 5086, 'license': 'MIT',
     'does': '42 FFmpeg tools with an MCP server: cut, captions, reframe, loudness, exports', 'jobs': ['cut', 'captions', 'reframe', 'audio', 'export'], 'status': 'recommend'},
    {'name': 'awesome-claude-video-skills', 'repo': 'zhuyansen/awesome-claude-video-skills', 'stars': 1918, 'license': 'see repo',
     'does': '180 video skill repos by type, security-graded', 'jobs': ['discover'], 'status': 'recommend'},
    {'name': 'claude-remotion-skill', 'repo': 'haidrrrry/claude-remotion-skill', 'stars': 954, 'license': 'MIT',
     'does': 'motion graphics, word-synced captions, sound design with Remotion', 'jobs': ['animation', 'captions', 'sound'], 'status': 'recommend'},
    {'name': 'hermes-video-editor', 'repo': 'oliverhees/hermes-video-editor', 'stars': None, 'license': 'PolyForm-Noncommercial-1.0.0',
     'does': 'native editable project for Hermes', 'jobs': ['edit', 'project'], 'status': 'in studio'},
    {'name': 'whisper.cpp', 'repo': 'ggml-org/whisper.cpp', 'stars': None, 'license': 'MIT',
     'does': 'local word-timed speech', 'jobs': ['captions', 'subtitles', 'transcribe'], 'status': 'in studio'},
    {'name': 'Eyes', 'repo': 'Agenziamaia/eyes', 'stars': None, 'license': 'MIT',
     'does': 'frame, motion and sound measurements', 'jobs': ['review', 'qa'], 'status': 'in studio'},
]


def recommend(request, limit=5):
    """Match a plain request ('animate my intro', 'brain rot captions') to skills, best first."""
    words = {w.strip('.,!?').lower() for w in request.split()}
    alias = {'subtitles': 'captions', 'animate': 'animation', 'animations': 'animation', 'sound': 'sound',
             'music': 'audio', 'trailer': 'trailer', 'recreate': 'recreate', 'copy': 'recreate', 'edit': 'edit'}
    want = {alias.get(w, w) for w in words}
    scored = []
    for s in SKILLS:
        hit = len(want & set(s['jobs']))
        if hit:
            scored.append((hit, s['stars'] or 0, s))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [{'name': s['name'], 'repo': f"https://github.com/{s['repo']}", 'status': s['status'], 'license': s['license'], 'does': s['does']}
            for _, _, s in scored[:limit]]
