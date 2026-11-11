"""Styles for studio_finish. A style is plain data, so new looks are one dict away.

Each style sets the intro treatment, caption look and pacing:
  intro_dark/intro_light  colours the first frame is dithered into
  box/text                caption box and text colours (box None = no box)
  size                    caption size in px at 1080 wide
  words                   words per caption chunk (brain rot = 1)
  y                       caption centre as a fraction of frame height
"""
from PIL import Image

STYLES = {
    'hermes-dither': {'label': 'Hermes dither', 'intro_dark': (20, 20, 220), 'intro_light': (70, 70, 255),
                      'box': (26, 26, 240), 'text': (255, 255, 255), 'size': 110, 'words': 3, 'y': 0.80,
                      'highlight': (255, 255, 255), 'highlight_text': (26, 26, 240), 'music_db': -20},
    'clean-short': {'label': 'Clean short', 'intro_dark': (12, 12, 12), 'intro_light': (235, 235, 235),
                    'box': (12, 12, 12), 'text': (255, 255, 255), 'size': 92, 'words': 4, 'y': 0.82,
                    'highlight': (255, 214, 0), 'highlight_text': (12, 12, 12), 'music_db': -24},
    'brain-rot': {'label': 'Brain rot', 'intro_dark': (5, 5, 5), 'intro_light': (255, 228, 0),
                  'box': (0, 0, 0), 'text': (255, 228, 0), 'size': 170, 'words': 1, 'y': 0.50, 'music_db': -15, 'split': True},
    'mono-kinetic': {'label': 'Mono kinetic', 'intro_dark': (0, 0, 0), 'intro_light': (255, 255, 255),
                     'box': (255, 255, 255), 'text': (0, 0, 0), 'size': 120, 'words': 2, 'y': 0.72},
}


def get(name):
    if isinstance(name, dict):
        return {**STYLES['hermes-dither'], **name}
    if name not in STYLES:
        raise ValueError(f'unknown style {name}; choose from {sorted(STYLES)} or pass a style dict')
    return STYLES[name]


def from_reference(image_path, name='from-reference'):
    """Recreate a look from a reference frame: its darkest, lightest and most saturated colours
    become the intro dither pair and the caption colours. A starting point, not a copy."""
    im = Image.open(image_path).convert('RGB').resize((160, 160))
    pal = im.quantize(colors=6, method=Image.Quantize.MEDIANCUT).convert('RGB')
    colours = sorted({c for _, c in pal.getcolors(160 * 160)}, key=lambda c: sum(c))
    dark, light = colours[0], colours[-1]
    accent = max(colours, key=lambda c: max(c) - min(c))
    # keep the dither readable: push the pair apart until they clearly differ
    dark = tuple(int(c * 0.45) for c in dark)
    light = tuple(min(255, int(c + (255 - c) * 0.35)) for c in light)
    text = (255, 255, 255) if sum(accent) < 380 else (10, 10, 10)
    return {'label': f'Look of {name}', 'intro_dark': dark, 'intro_light': light,
            'box': accent, 'text': text, 'highlight': light, 'highlight_text': dark,
            'size': 110, 'words': 3, 'y': 0.80}
