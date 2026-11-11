"""Discover real media in explicitly selected local library folders."""
import os
from pathlib import Path
import re

KINDS = {'.mp4': 'video', '.mov': 'video', '.mkv': 'video', '.webm': 'video',
         '.wav': 'audio', '.mp3': 'audio', '.m4a': 'audio', '.flac': 'audio',
         '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.webp': 'image',
         '.svg': 'vector', '.lottie': 'animation', '.riv': 'animation'}


def discover(roots, query='', kind=None, limit=20):
    if not isinstance(roots, list) or not roots or len(roots) > 12:
        raise ValueError('Supply one to twelve explicit local library folders')
    terms = set(re.findall(r'[a-z0-9]+', query.casefold()))
    rows, scanned = [], 0
    for root in roots:
        root = Path(root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Library root must be a directory')
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__'))
            for name in sorted(files):
                path = Path(folder) / name
                media_kind = KINDS.get(path.suffix.casefold())
                if not media_kind:
                    continue
                scanned += 1
                if scanned > 100000:
                    raise ValueError('Library scan exceeds 100,000 media files; narrow the supplied roots')
                if kind and media_kind != kind:
                    continue
                score = len(terms & set(re.findall(r'[a-z0-9]+', name.casefold())))
                if terms and not score:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                rows.append({'path': str(path.resolve()), 'name': name, 'kind': media_kind,
                             'bytes': st.st_size, 'filename_matches': score})
    rows.sort(key=lambda r: (-r['filename_matches'], r['name'], r['path']))
    return {'scanned_media': scanned, 'matched_media': len(rows), 'results': rows[:max(1, min(int(limit), 100))],
            'method': 'Filename and media-extension search, not semantic vision or license/approval detection',
            'roots': [str(Path(r).expanduser().resolve()) for r in roots]}
