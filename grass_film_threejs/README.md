# The growing does not stop · three.js edition

A self-contained, procedural **91-second / nine-shot grass-field film**. No stock
images, textures, footage, or Blender renders are used by the page. The original
fourteen narration lines and their start times are retained in `narration.json`.

## Install and view

Requirements: Node **22.12+** (tested with 24), Python 3.10+, ffmpeg, and a WebGL2
browser. For generation without the original audio, also install `numpy edge-tts`.

```sh
cd ~/repos/blender/grass_film_threejs
# On this machine Node is managed by nvm:
source ~/.nvm/nvm.sh
npm ci
npm run prepare-film
npm run serve
```

Open **http://127.0.0.1:8030** and click **Play film** to unlock audio. Pause,
resume, seek, replay, and optional narration captions are provided. The audio
element's `currentTime` is the only playback clock: slower devices skip visual
frames rather than desynchronizing the narration. Hardware WebGL is recommended
for real-time playback; software GL capture deliberately takes longer than 91s.
`PORT=8040 npm run serve` changes the port. The server binds only to localhost.

`prepare.py` copies **three.js 0.170.0** and its addon modules from the pinned npm
installation to ignored `vendor/`. Thus the prepared page needs no CDN or build
server: any static HTTP server can serve this folder offline. It is not a
`file://` page, since browsers restrict module/fetch loading there.

## Sound

By default preparation reuses `~/grassfilm/audio/soundtrack.wav`, the original
narration + synthesized wind/bird ambience. An alternative can be supplied with
`python3 prepare.py --audio /path/to/mix.wav`. The browser copy is a 192 kb/s MP3
in ignored `media/`; it is not committed. If the source is absent, the same mix is
regenerated from the bundled original text/timings, Edge TTS `en-US-GuyNeural`
(-10% rate, -4 Hz pitch), and seeded numpy ambience. Initial generation needs
internet access to Edge TTS. Remove affected `media/line_*.mp3` cache files when
changing voice/text. Overlapping narration windows cause an error.

## Preview, inspect, capture

Chrome/Chromium must be installed. `CHROME_BIN` selects its executable; the
default is `/usr/bin/google-chrome`. On this machine use:

```sh
export CHROME_BIN=/opt/.devin/chrome/chrome/linux-156.0.8078.47/chrome-linux64/chrome
npm run stills
# View ~/grassfilm/stills_threejs/shot_01.png ... shot_09.png before continuing.
node capture.mjs --check-player
npm run capture
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate -of json ~/grassfilm/grass_field_threejs.mp4
```

`FILM_ROOT=/another/output/root` relocates previews and the final MP4. Both
commands start/stop their own localhost server on a free port, and use pinned
Puppeteer Core **25.18.1**, headless Chrome and ANGLE SwiftShader software GL.
Capture produces **all 2,184 unique browser frames at 1920×1080 / 24 fps**,
piping lossless PNGs to ffmpeg, without optical-flow interpolation or dropped
frames. ffmpeg muxes the prepared audio as AAC with H.264 yuv420p video and
fast-start metadata. Output: `~/grassfilm/grass_field_threejs.mp4`.

Do not interrupt capture if you need the finished MP4; an interrupted pipe can
leave an incomplete file. Rerunning overwrites the output. Media, dependencies,
vendor files and caches are ignored. `npm ci && npm run prepare-film` recreates
all the assets required by the page.

## Deterministic interface and scene design

Open `/?capture=1` to hide all interface/captions and disable animation/audio.
Wait for `window.film.ready`, then call:

```js
window.film.renderFrame(N); // integer 0..2183; t = N / 24
window.film.renderAt(t);    // seconds, clamped to 0..91; non-finite values rejected
```

There is no accumulated simulation or wall-clock delta. Seeded geometry,
vertex-shader wind waves, seed-head bending, rain and firefly motion are pure
functions of `t`. Previews also test repeat-frame stability after a jump to a
different shot and rejection of empty/invalid times. The repeat-image check
allows a mean 8-bit channel delta of at most 0.001 and at most 0.02% changed
pixels. Tiny raster differences were observed in this Chrome/SwiftShader build;
byte-identical PNGs are not guaranteed even on the same renderer. Capture time
and procedural state do not depend on frame order or capture speed.

`meadow.js` builds 65,000 instanced folded/tapered blades, instanced seed heads
and awns, procedural gradient sky, fog, physically shaded dew with a generated
reflection environment, rain streaks and emissive firefly particles. `film.js`
defines nine 11-second shots starting at 0, 10, …, 80 seconds. One-second
crossfades are rendered into separate targets, with per-shot light, fog, warm
grading and exposure. EffectComposer combines BokehPass (a denser disk kernel,
with wind-matched instanced depth), UnrealBloomPass, OutputPass and FXAA.
Night uses bloom without DOF to preserve small, soft firefly lights. The look
follows the Blender film's compositions and calm dawn-to-night arc, rather than
claiming equivalence with offline path-traced refraction or illumination.
