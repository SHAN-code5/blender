# The growing does not stop

A 91-second procedural grass-field day: nine eleven-second shots with eight
one-second crossfades. Everything visible is generated with Blender's Python
API: ridged tapered grass meshes scattered with Geometry Nodes, shape-key wind,
seed heads and awns, water droplets, rain and fireflies. No image/video assets.
Nishita sky, transmitted leaf light, depth-based atmospheric haze, camera DOF,
slow camera travel, and restrained highlight bloom form the photographic look.

## Install

Requires Linux, Python 3.10+, ffmpeg with `minterpolate`, numpy and edge-tts.
Download the official Blender 4.5.3 LTS Linux x64 tarball from
https://download.blender.org/release/Blender4.5/blender-4.5.3-linux-x64.tar.xz
and extract it as `~/grassfilm/blender` (binary `~/grassfilm/blender/blender`).

```sh
python -m pip install numpy edge-tts
python grass_film/build.py stills
# Inspect ~/grassfilm/stills/shot_01.png through shot_09.png BEFORE rendering.
python grass_film/build.py audio
python grass_film/build.py render
python grass_film/build.py assemble
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate -of json ~/grassfilm/grass_field.mp4
```

`--root PATH` moves all generated media; `--blender PATH` overrides the binary.
`scene.py --shot 0..8 --output PATH --still` can be passed after Blender's `--`.
Scene seeds are deterministic. Each render stage writes separate per-shot logs.
Completed 44-frame shot directories are skipped on resume. Blender errors or
missing stills stop the build; remove incomplete shot frames before changing a
scene. Never put the output root in the source folder.

## CPU budget

Cycles CPU, eight threads, six samples and OpenImageDenoise, full 1920×1080.
Four unique procedural frames per second are motion-compensated to 24 fps by
ffmpeg. This intentionally spends the CPU budget on full-resolution geometry,
DOF and lighting rather than reducing resolution. Wind and camera movement are
slow; interpolation can still soften fine blades during crossovers. No repeated
still-image pans or borrowed footage. EEVEE can be selected with `--engine
BLENDER_EEVEE_NEXT` on machines with a working headless graphics driver.

## Sound and timing

`narration.py` contains the original fourteen lines and their start times. Edge
TTS `en-US-GuyNeural`, -10% rate, -4 Hz pitch; internet access is needed only
for initial voice generation. Generated line MP3s are cached; delete the affected
line file after changing text or voice settings. The build rejects overlapping
lines. The 48 kHz stereo bed is seeded filtered numpy noise with synthesized,
quiet bird chirps, faded at both ends; no stock recordings or music. Final audio
is AAC 192 kb/s and video H.264, yuv420p with fast-start metadata.

Outputs, Blender itself and voice cache are deliberately not tracked in Git.
