"""Local render of a timeline with FFmpeg: trim each cut, scale-crop to the frame, concatenate."""
import subprocess
from pathlib import Path
from .timeline import Timeline


def _has_audio(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout
    return bool(out.strip())


def render_command(tl: Timeline, output):
    if not tl.cuts:
        raise ValueError('timeline has no cuts')
    sources = list(dict.fromkeys(c.source for c in tl.cuts))
    for s in sources:
        if not Path(s).is_file():
            raise FileNotFoundError(s)
    argv = ['ffmpeg', '-nostdin', '-hide_banner', '-y']
    for s in sources:
        argv += ['-i', s]
    w, h, fps = tl.width, tl.height, tl.fps
    parts, labels = [], ''
    for i, c in enumerate(tl.cuts):
        k = sources.index(c.source)
        parts.append(f'[{k}:v]trim=start={c.start}:end={c.end},setpts=PTS-STARTPTS,'
                     f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={fps},setsar=1[v{i}]')
        if _has_audio(c.source):
            parts.append(f'[{k}:a]atrim=start={c.start}:end={c.end},asetpts=PTS-STARTPTS,aresample=48000[a{i}]')
        else:
            parts.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={c.duration}[a{i}]')
        labels += f'[v{i}][a{i}]'
    parts.append(f'{labels}concat=n={len(tl.cuts)}:v=1:a=1[v][a]')
    return argv + ['-filter_complex', ';'.join(parts), '-map', '[v]', '-map', '[a]',
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(output)]


def render(tl: Timeline, output, timeout=1800):
    argv = render_command(tl, output)
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-2000:])
    return str(output)
