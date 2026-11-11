"""Final Cut Pro and DaVinci Resolve: both import FCPXML.

Export only for now. Import back from an editor goes through OTIO.
"""
from fractions import Fraction
from pathlib import Path
from xml.sax.saxutils import quoteattr
from .timeline import Timeline


def _t(seconds, fps):
    frames = round(seconds * fps)
    f = Fraction(frames * 1000, int(round(fps * 1000)))
    return f'{f.numerator}/{f.denominator}s' if f.denominator != 1 else f'{f.numerator}s'


def to_fcpxml(tl: Timeline) -> str:
    fps = tl.fps
    frame = _t(1 / fps, fps)
    sources = list(dict.fromkeys(c.source for c in tl.cuts))
    assets = '\n'.join(
        f'    <asset id="a{i}" name={quoteattr(Path(s).stem)} hasVideo="1" hasAudio="1" format="f1">'
        f'<media-rep kind="original-media" src={quoteattr(Path(s).resolve().as_uri())}/></asset>'
        for i, s in enumerate(sources))
    offset, clips = 0.0, []
    for c in tl.cuts:
        clips.append(f'          <asset-clip ref="a{sources.index(c.source)}" name={quoteattr(c.name or Path(c.source).stem)} '
                     f'offset="{_t(offset, fps)}" start="{_t(c.start, fps)}" duration="{_t(c.duration, fps)}"/>')
        offset += c.duration
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10">\n  <resources>\n'
            f'    <format id="f1" frameDuration="{frame}" width="{tl.width}" height="{tl.height}"/>\n{assets}\n  </resources>\n'
            f'  <library><event name="Hermes Studio"><project name={quoteattr(tl.name)}>\n'
            f'      <sequence format="f1" duration="{_t(tl.duration, fps)}"><spine>\n' + '\n'.join(clips) +
            '\n      </spine></sequence>\n    </project></event></library>\n</fcpxml>\n')


def write_fcpxml(tl: Timeline, path):
    Path(path).write_text(to_fcpxml(tl))
    return str(path)
