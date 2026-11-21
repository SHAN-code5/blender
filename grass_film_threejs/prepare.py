"""Vendor pinned three.js and prepare the original film's audio, never in Git."""
import argparse
import asyncio
import json
import shutil
import subprocess
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent


async def generate(out):
    import edge_tts
    import numpy as np

    lines = json.loads((HERE / 'narration.json').read_text())
    sr = 48000
    voice = np.zeros(sr * 91, dtype=np.float32)
    for i, (start, line) in enumerate(lines):
        path = out.parent / f'line_{i:02d}.mp3'
        if not path.exists():
            await edge_tts.Communicate(line, 'en-US-GuyNeural', rate='-10%', pitch='-4Hz').save(str(path))
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'f32le', '-ac', '1', '-ar', str(sr), '-'])
        data = np.frombuffer(raw, dtype='<f4')
        end = int(start * sr) + len(data)
        limit = lines[i + 1][0] if i + 1 < len(lines) else 91
        if end > int(limit * sr):
            raise ValueError(f'Narration line {i} exceeds its timing window')
        voice[int(start * sr):end] += data
    rng = np.random.default_rng(730)
    t = np.arange(sr * 91) / sr
    smooth = np.convolve(rng.normal(0, 1, len(t)), np.ones(140) / 140, mode='same')
    wind = smooth * (.045 + .018 * np.sin(t * .47) + .009 * np.sin(t * .13))
    left, right = wind.copy(), np.roll(wind, 621)
    for start in [3, 9, 16, 25, 37, 45, 67, 74]:
        for j in range(3):
            tt = np.arange(int(.14 * sr)) / sr
            call = np.sin(2 * np.pi * (2300 * tt + 1700 * tt**2)) * np.sin(np.pi * tt / .14)**2 * .009
            a = int((start + j * .23) * sr)
            left[a:a + len(call)] += call
            right[a:a + len(call)] += call * .55
    fade = np.minimum(np.minimum(t / 2, (91 - t) / 3), 1).clip(0, 1)
    stereo = np.stack([voice * .88 + left, voice * .88 + right], axis=1) * fade[:, None]
    peak = np.max(np.abs(stereo))
    if peak > .92:
        stereo *= .92 / peak
    with wave.open(str(out), 'wb') as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((stereo * 32767).astype('<i2').tobytes())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--audio', type=Path, default=Path.home() / 'grassfilm/audio/soundtrack.wav')
    a = p.parse_args()
    three = HERE / 'node_modules/three'
    if not three.exists():
        raise SystemExit('Run npm ci in grass_film_threejs first.')
    vendor = HERE / 'vendor'
    vendor.mkdir(exist_ok=True)
    shutil.copy2(three / 'build/three.module.js', vendor / 'three.module.js')
    shutil.copytree(three / 'examples/jsm', vendor / 'addons', dirs_exist_ok=True)
    media = HERE / 'media'
    media.mkdir(exist_ok=True)
    if a.audio.exists():
        source = a.audio
        print(f'Reusing original narration + ambience: {source}')
    else:
        source = media / 'soundtrack.wav'
        asyncio.run(generate(source))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-t', '91', '-c:a', 'libmp3lame', '-b:a', '192k', str(media / 'soundtrack.mp3')], check=True)
    print('Ready: vendored three.js 0.170.0 and 91s soundtrack')


if __name__ == '__main__':
    main()
