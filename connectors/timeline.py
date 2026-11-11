"""One shared timeline for every connector.

The interchange file is OpenTimelineIO JSON, written without importing the
opentimelineio package so the plugin has no new dependency. Tests read the
output back with the real library when it is installed.
"""
from dataclasses import dataclass, field, asdict
import json
from pathlib import Path


@dataclass
class Cut:
    source: str          # absolute media path
    start: float         # seconds into the source
    end: float           # seconds into the source
    name: str = ''

    def __post_init__(self):
        if self.end <= self.start:
            raise ValueError(f'cut {self.name or self.source}: end {self.end} must be after start {self.start}')

    @property
    def duration(self):
        return self.end - self.start


@dataclass
class Timeline:
    name: str
    fps: float = 30.0
    width: int = 1080
    height: int = 1920
    cuts: list = field(default_factory=list)

    @property
    def duration(self):
        return sum(c.duration for c in self.cuts)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(name=d['name'], fps=d.get('fps', 30.0), width=d.get('width', 1080), height=d.get('height', 1920),
                   cuts=[Cut(**c) for c in d.get('cuts', [])])


def _rt(seconds, fps):
    return {'OTIO_SCHEMA': 'RationalTime.1', 'rate': float(fps), 'value': round(seconds * fps, 6)}


def _range(start, duration, fps):
    return {'OTIO_SCHEMA': 'TimeRange.1', 'start_time': _rt(start, fps), 'duration': _rt(duration, fps)}


def to_otio(tl: Timeline) -> dict:
    clips = [{
        'OTIO_SCHEMA': 'Clip.2', 'name': c.name or Path(c.source).stem, 'metadata': {'hermes_studio': {'source_seconds': [c.start, c.end]}},
        'source_range': _range(c.start, c.duration, tl.fps), 'effects': [], 'markers': [], 'enabled': True,
        'media_references': {'DEFAULT_MEDIA': {'OTIO_SCHEMA': 'ExternalReference.1', 'name': '', 'metadata': {},
                                               'available_range': None, 'available_image_bounds': None,
                                               'target_url': Path(c.source).resolve().as_uri()}},
        'active_media_reference_key': 'DEFAULT_MEDIA'} for c in tl.cuts]
    track = {'OTIO_SCHEMA': 'Track.1', 'name': 'V1', 'kind': 'Video', 'metadata': {}, 'source_range': None,
             'effects': [], 'markers': [], 'enabled': True, 'children': clips}
    return {'OTIO_SCHEMA': 'Timeline.1', 'name': tl.name, 'global_start_time': None,
            'metadata': {'hermes_studio': {'fps': tl.fps, 'width': tl.width, 'height': tl.height}},
            'tracks': {'OTIO_SCHEMA': 'Stack.1', 'name': 'tracks', 'metadata': {}, 'source_range': None,
                       'effects': [], 'markers': [], 'enabled': True, 'children': [track]}}


def from_otio(d: dict) -> Timeline:
    if d.get('OTIO_SCHEMA', '').split('.')[0] != 'Timeline':
        raise ValueError('not an OTIO timeline')
    meta = d.get('metadata', {}).get('hermes_studio', {})
    video = [t for t in d['tracks']['children'] if t.get('kind') == 'Video']
    if not video:
        raise ValueError('timeline has no video track')
    cuts = []
    for child in video[0]['children']:
        if child.get('OTIO_SCHEMA', '').split('.')[0] != 'Clip':
            continue  # gaps and transitions are not cuts
        rng = child['source_range']
        rate = rng['start_time']['rate']
        start = rng['start_time']['value'] / rate
        ref = child.get('media_references', {}).get(child.get('active_media_reference_key', 'DEFAULT_MEDIA')) \
            or child.get('media_reference') or {}
        url = ref.get('target_url', '')
        source = url[7:] if url.startswith('file://') else url
        from urllib.parse import unquote
        cuts.append(Cut(source=unquote(source), start=round(start, 6),
                        end=round(start + rng['duration']['value'] / rate, 6), name=child.get('name', '')))
    return Timeline(name=d.get('name', ''), fps=meta.get('fps', video[0]['children'][0]['source_range']['start_time']['rate'] if video[0]['children'] else 30.0),
                    width=meta.get('width', 1080), height=meta.get('height', 1920), cuts=cuts)


def write_otio(tl: Timeline, path):
    Path(path).write_text(json.dumps(to_otio(tl), indent=2))
    return str(path)


def read_otio(path) -> Timeline:
    return from_otio(json.loads(Path(path).read_text()))
