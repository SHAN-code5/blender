"""Render stills first; after visual review, render and assemble the 91s film."""
import argparse
import subprocess
from pathlib import Path

HERE=Path(__file__).resolve().parent

def run(*args):
    print(' '.join(map(str,args)),flush=True)
    subprocess.run(list(map(str,args)),check=True)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('stage',choices=['stills','render','assemble','audio'])
    p.add_argument('--root',type=Path,default=Path.home()/'grassfilm')
    p.add_argument('--blender',type=Path)
    a=p.parse_args();root=a.root;blender=a.blender or root/'blender/blender'
    root.mkdir(parents=True,exist_ok=True)
    if a.stage=='audio':
        import sys
        run(sys.executable,HERE/'narration.py','--output',root/'audio')
    if a.stage in ['stills','render']:
        for shot in range(9):
            out=root/'stills' if a.stage=='stills' else root/'render'/f'shot_{shot+1:02d}'
            out.mkdir(parents=True,exist_ok=True)
            if a.stage=='render' and len(list(out.glob('frame_*.png')))==44:
                continue
            log=root/f'{a.stage}_{shot+1:02d}.log'
            cmd=[str(blender),'-b','-t','8','-P',str(HERE/'scene.py'),'--','--shot',str(shot),'--output',str(out)]
            if a.stage=='stills':cmd+=['--still']
            print(f'{a.stage}: shot {shot+1}/9 → {log}',flush=True)
            with log.open('w') as f:
                subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
            if a.stage=='stills' and not (out/f'shot_{shot+1:02d}.png').exists():
                raise RuntimeError(f'Missing still; inspect {log}')
            if a.stage=='render' and len(list(out.glob('frame_*.png')))!=44:
                raise RuntimeError(f'Incomplete render; inspect {log}')
    if a.stage=='assemble':
        clips=[]
        for shot in range(9):
            clip=root/'render'/f'clip_{shot+1:02d}.mp4';clips.append(clip)
            run('ffmpeg','-v','warning','-y','-threads','8','-framerate','4','-i',root/'render'/f'shot_{shot+1:02d}'/'frame_%04d.png',
                '-vf','minterpolate=fps=24:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,tpad=stop_mode=clone:stop_duration=1',
                '-t','11','-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',clip)
        cmd=['ffmpeg','-v','warning','-y','-filter_complex_threads','2']
        for clip in clips:cmd+=['-i',str(clip)]
        cmd+=['-i',str(root/'audio/soundtrack.wav')]
        filters=[];last='0:v'
        for shot in range(1,9):
            dest=f'v{shot}'
            filters.append(f'[{last}][{shot}:v]xfade=transition=fade:duration=1:offset={shot*10}[{dest}]')
            last=dest
        filters.append(f'[{last}]fade=t=in:st=0:d=1.5,fade=t=out:st=88.5:d=2.5[film]')
        cmd+=['-filter_complex',';'.join(filters),'-map','[film]','-map','9:a','-t','91','-c:v','libx264','-threads','8','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart',str(root/'grass_field.mp4')]
        run(*cmd)

if __name__=='__main__':main()
