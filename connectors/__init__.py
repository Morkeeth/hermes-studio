"""Connector registry: one timeline, many tools. Status is what actually runs, not what is planned."""
from .timeline import Cut, Timeline, to_otio, from_otio, write_otio, read_otio
from .fcpxml import to_fcpxml, write_fcpxml
from . import ffmpeg_render, generate, doctor, finish, styles, skills, gen_api, demo

CONNECTORS = [
    # group, name, status, route
    ('editor', 'ffmpeg', 'working', 'local render of the shared timeline'),
    ('editor', 'opentimelineio', 'working', 'OTIO JSON export and import; the hub every editor goes through'),
    ('editor', 'final-cut-pro', 'export only', 'FCPXML 1.10 export; import back via OTIO'),
    ('editor', 'davinci-resolve', 'export only', 'imports the same FCPXML or OTIO; scripting API not wired'),
    ('editor', 'premiere-pro', 'planned', 'OTIO or XML interchange; needs a licence to test'),
    ('editor', 'hermes-video-editor', 'working', 'existing studio_cut tools'),
    ('motion', 'lottie', 'guidance', 'motion-design skill informs timing; no renderer wired'),
    ('motion', 'motion-canvas', 'planned', 'scouted, not run'),
    ('motion', 'revideo', 'planned', 'scouted, not run'),
    ('motion', 'manim', 'planned', 'scouted, not run'),
    ('generate', 'higgsfield', 'dry run', 'plans requests; never sends; paid calls need the owner\'s yes'),
    ('audio', 'whisper.cpp', 'working', 'existing studio_take'),
    ('review', 'eyes', 'working', 'existing studio_gate frame and sound checks'),
]


def describe(group=None):
    return [{'group': g, 'name': n, 'status': s, 'route': r} for g, n, s, r in CONNECTORS if group in (None, g)]
