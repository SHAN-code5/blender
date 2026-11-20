"""Original, shot-timed Edge narration and deterministic stereo field ambience."""
import asyncio
import json
import subprocess
import wave
from pathlib import Path

import edge_tts
import numpy as np

LINES = [
    (1.5, 'Before the light, the meadow is a sea without a shore.'),
    (7.0, 'Every blade holds a little of the night.'),
    (12.0, 'Then warmth begins to move across the field.'),
    (17.5, 'The grass answers, one stem at a time.'),
    (22.0, 'A whole sky rests inside a drop of dew.'),
    (28.0, 'For a moment, nothing falls.'),
    (33.0, 'Above the green, the seed heads open to the air.'),
    (42.0, 'By noon, the wind has found its way through everything.'),
    (48.0, 'Wave follows wave. There is no hurry here.'),
    (53.0, 'A passing shower darkens the earth beneath them.'),
    (62.5, 'And when the clouds move on, each slender stem is gold.'),
    (72.0, 'The day lets go slowly, leaving its warmth among the seeds.'),
    (81.5, 'Small lights rise where the shadows gather.'),
    (85.5, 'The field grows quiet. The growing does not stop.'),
]
DURATION = 91

async def build(root):
    root.mkdir(parents=True, exist_ok=True)
    sr=48000
    voice=np.zeros(sr*DURATION,dtype=np.float32)
    for i,(start,line) in enumerate(LINES):
        mp3=root/f'line_{i:02d}.mp3'
        if not mp3.exists():
            tmp=mp3.with_suffix('.part')
            await edge_tts.Communicate(line,'en-US-GuyNeural',rate='-10%',pitch='-4Hz').save(str(tmp))
            tmp.replace(mp3)
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(mp3),'-f','f32le','-ac','1','-ar',str(sr),'-'])
        data=np.frombuffer(raw,dtype='<f4')
        end=int(start*sr)+len(data)
        next_start=LINES[i+1][0] if i+1<len(LINES) else DURATION
        if end>int(next_start*sr):
            raise ValueError(f'Line {i} exceeds its narration window: {len(data)/sr:.2f}s')
        voice[int(start*sr):end]+=data
    rng=np.random.default_rng(730)
    count=sr*DURATION
    # Filtered noise has no pitched drone: a slow, breathing wind bed.
    noise=rng.normal(0,1,count)
    smooth=np.convolve(noise,np.ones(140)/140,mode='same')
    t=np.arange(count)/sr
    wind=smooth*(.045+.018*np.sin(t*.47)+.009*np.sin(t*.13))
    left=wind.copy();right=np.roll(wind,621)
    for start in [3,9,16,25,37,45,67,74]:
        for j in range(3):
            tt=np.arange(int(.14*sr))/sr
            call=np.sin(2*np.pi*(2300*tt+1700*tt**2))*np.sin(np.pi*tt/.14)**2*.009
            a=int((start+j*.23)*sr)
            left[a:a+len(call)]+=call
            right[a:a+len(call)]+=call*.55
    fade=np.minimum(np.minimum(t/2,(DURATION-t)/3),1).clip(0,1)
    stereo=np.stack([voice*.88+left,voice*.88+right],axis=1)*fade[:,None]
    peak=np.max(np.abs(stereo))
    if peak>.92:stereo*=.92/peak
    with wave.open(str(root/'soundtrack.wav'),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(sr);f.writeframes((stereo*32767).astype('<i2').tobytes())
    (root/'narration.json').write_text(json.dumps(LINES,indent=2))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    asyncio.run(build(p.parse_args().output))
