"""The take tree: every version of one project, built from the gateway's own job records.

A reply to any delivered video already names its parent job. This module turns those
parent links into a tree with one stable label per version (V1, V2, V2b), so a branch
from an older video gets its own name instead of a second "Version 2".

graph() and snapshot() use the standard library only (they run on the gateway).
render() needs Pillow and runs on the workstation. Only whitelisted fields leave a job:
never the query, because an approve job holds YES plus the one-time code.
"""
import json
from pathlib import Path

VIDEO = ('finished', 'revised', 'generated')
FIELDS = ('id', 'job', 'option', 'parent', 'kind', 'label', 'what', 'created_at')


def _res(job):
    return job.get('result') or {}


def _video_parent(job, by):
    """The delivered video this job was made from: an offered option or an earlier version."""
    p, seen = by.get(job.get('parent')), set()
    while p and p['id'] not in seen:
        seen.add(p['id'])
        r = _res(p)
        if r.get('state') == 'alternatives_ready':
            opts = {o['option']: o for o in r.get('options') or []}
            style = str(job.get('style') or '').strip()
            if len(style) == 1 and style.upper() in opts:
                return p['id'] + ':' + style.upper()
            named = [k for k, o in sorted(opts.items()) if o.get('style') in (style, _res(job).get('style'))]
            return p['id'] + ':' + named[0] if named else None
        if r.get('state') in VIDEO:
            return p['id']
        p = by.get(p.get('parent'))  # a quote is not a video: walk past it
    return None


def _what(job):
    r = _res(job)
    if r.get('state') == 'revised':
        changed = [c for c in r.get('changed') or [] if not str(c).startswith('style ')] or list(r.get('changed') or [])
        text = '; '.join(map(str, changed))
        if r.get('skipped'):
            text += '. Could not: ' + '; '.join(str(s).split(':')[0] for s in r['skipped'])
        return text[:90]
    if r.get('state') == 'generated':
        return 'generated opening'
    return 'style ' + str(r.get('style') or '')


def graph(jobs):
    """{node id: node} for every delivered option and version, labelled per project."""
    by = {j['id']: j for j in jobs if j.get('state') == 'completed'}
    nodes = {}
    for j in by.values():
        r = _res(j)
        if r.get('state') == 'alternatives_ready':
            for o in r.get('options') or []:
                nid = j['id'] + ':' + o['option']
                nodes[nid] = {'id': nid, 'job': j['id'], 'option': o['option'], 'parent': None, 'kind': 'option',
                              'label': o['option'], 'what': str(o.get('label') or '')[:40], 'created_at': j.get('created_at', 0)}
        elif r.get('state') in VIDEO:
            nodes[j['id']] = {'id': j['id'], 'job': j['id'], 'option': None, 'parent': _video_parent(j, by), 'kind': 'version',
                              'what': _what(j), 'created_at': j.get('created_at', 0), 'label': j.get('version')}
    for n in nodes.values():
        if n['parent'] not in nodes:
            n['parent'] = None
    groups = {}
    for n in nodes.values():
        groups.setdefault(project(nodes, n['id']), []).append(n)
    for members in groups.values():
        _label(nodes, members)
    return nodes


def _depth(nodes, n):
    d, p = 1, nodes.get(n['parent'])
    while p and p['kind'] == 'version':
        d, p = d + 1, nodes.get(p['parent'])
    return d


def _label(nodes, members):
    """V<depth>, then V<depth>b, V<depth>c for later versions at the same depth.

    A name already sent in a caption is frozen in the job record ('version') and kept, so the
    caption and the tree never disagree. The other versions take the free names, oldest first.
    """
    rows = {}
    for n in members:
        if n['kind'] == 'version':
            rows.setdefault(_depth(nodes, n), []).append(n)
    for d, row in rows.items():
        names = ['V%d%s' % (d, '' if i == 0 else chr(ord('a') + i) if i < 26 else str(i)) for i in range(len(row) + 26)]
        taken = {n['label'] for n in row if n.get('label')}
        free = [x for x in names if x not in taken]
        for n in sorted(row, key=lambda x: (x['created_at'], x['id'])):
            if not n.get('label'):
                n['label'] = free.pop(0)


def project(nodes, nid):
    """The key of the project a node belongs to: its top option's job, or its top version."""
    n, seen = nodes[nid], set()
    while n['parent'] in nodes and n['id'] not in seen:
        seen.add(n['id'])
        n = nodes[n['parent']]
    return n['job'] if n['kind'] == 'option' else n['id']


def load(root):
    jobs = []
    for path in sorted((Path(root) / 'jobs').glob('*.json')):
        try:
            jobs.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return jobs


def label(root, job_id):
    """(label, parent label) of a delivered version, for its Telegram caption. (None, None) if unknown."""
    nodes = graph(load(root))
    n = nodes.get(job_id)
    if not n:
        return None, None
    p = nodes.get(n['parent'])
    return n.get('label'), (p or {}).get('label')


def snapshot(root, job_id=None):
    """Whitelisted nodes of one project: the one holding job_id, else the one with the newest version."""
    nodes = graph(load(root))
    if not nodes:
        return []
    hits = [n for n in nodes.values() if n['job'] == job_id] if job_id else []
    start = hits[0] if hits else max(nodes.values(), key=lambda n: (n['created_at'], n['id']))
    key = project(nodes, start['id'])
    keep = [n for n in nodes.values() if project(nodes, n['id']) == key]
    keep.sort(key=lambda n: (n['kind'] != 'option', n['created_at'], n['id']))
    return [{k: n.get(k) for k in FIELDS} for n in keep]


# Drawing. Hermes Studio brand: blue #1e1ed7, navy #20205f, white, DIN Condensed.
BLUE, NAVY, WHITE, DIM = (30, 30, 215), (32, 32, 95), (255, 255, 255), (190, 196, 255)
DISPLAY = ['/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf']
TEXT = ['/System/Library/Fonts/Supplemental/Arial Narrow.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf']
TW, TH, COL, ROW, LEFT, TOP = 108, 192, 250, 320, 60, 150


def _font(candidates, size):
    from PIL import ImageFont
    for f in candidates:
        if Path(f).is_file():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def layout(nodes):
    """(column, row) per node id. Columns are depth; each leaf gets a row; a parent sits beside its first child."""
    ids = {n['id'] for n in nodes}
    kids = {}
    for n in nodes:
        kids.setdefault(n['parent'] if n['parent'] in ids else None, []).append(n['id'])
    pos, nxt = {}, [0]

    def place(nid, col):
        children = kids.get(nid, [])
        if not children:
            pos[nid] = (col, nxt[0]); nxt[0] += 1
            return
        for c in children:
            place(c, col + 1)
        pos[nid] = (col, pos[children[0]][1])
    for top in kids.get(None, []):
        place(top, 0)
    return pos


def _wrap(text, width):
    lines, line = [], ''
    for word in str(text).split():
        if len(line) + len(word) + 1 > width and line:
            lines.append(line); line = word
        else:
            line = (line + ' ' + word).strip()
    return (lines + [line])[:3] if line or lines else []


def render(nodes, thumbs, out, title='Take tree'):
    """One PNG of the whole tree. thumbs maps node id to a frame image path, or is missing for a grey card."""
    from PIL import Image, ImageDraw, ImageOps
    pos = layout(nodes)
    cols = max(c for c, _ in pos.values()) + 1
    rows = max(r for _, r in pos.values()) + 1
    w, h = max(LEFT * 2 + cols * COL - (COL - TW), 900), TOP + rows * ROW + 20
    img = Image.new('RGB', (w, h), BLUE)
    d = ImageDraw.Draw(img)
    big, mid, small = _font(DISPLAY, 64), _font(DISPLAY, 34), _font(TEXT, 19)
    d.text((LEFT, 34), title.upper(), font=big, fill=WHITE)
    versions = [n for n in nodes if n['kind'] == 'version']
    d.text((LEFT, 104), f'{len(versions)} versions. Reply to any video in the chat to start a new version from it.', font=small, fill=DIM)
    newest = max(versions, key=lambda n: (n['created_at'], n['id']))['id'] if versions else None

    def box(nid):
        c, r = pos[nid]
        return LEFT + c * COL, TOP + r * ROW
    for n in nodes:
        if n['parent'] in pos:
            (px, py), (cx, cy) = box(n['parent']), box(n['id'])
            a, b = (px + TW, py + TH // 2), (cx, cy + TH // 2)
            mx = (a[0] + b[0]) // 2
            d.line([a, (mx, a[1]), (mx, b[1]), b], fill=DIM, width=3, joint='curve')
    for n in nodes:
        x, y = box(n['id'])
        card = Image.new('RGB', (TW, TH), NAVY)
        src = thumbs.get(n['id'])
        if src and Path(src).is_file():
            card = ImageOps.fit(Image.open(src).convert('RGB'), (TW, TH))
        mask = Image.new('L', (TW, TH), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, TW - 1, TH - 1], 12, fill=255)
        img.paste(card, (x, y), mask)
        if n['id'] == newest:
            d.rounded_rectangle([x - 4, y - 4, x + TW + 3, y + TH + 3], 15, outline=WHITE, width=4)
        d.text((x, y + TH + 8), str(n.get('label') or ''), font=mid, fill=WHITE)
        if n['id'] == newest:
            lw = d.textlength(str(n.get('label') or ''), font=mid)
            d.text((x + lw + 10, y + TH + 18), 'newest', font=small, fill=DIM)
        for i, line in enumerate(_wrap(n.get('what') or '', 24)):
            d.text((x, y + TH + 46 + i * 21), line, font=small, fill=DIM)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
