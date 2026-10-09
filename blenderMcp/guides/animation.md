# Animation

## Keyframes

`insert_keyframes(object_name="Ball", keyframes=[...], interpolation="bezier")` keys what
each entry gives at its `frame`:

```json
[{"frame": 1, "location": [0, 0, 3]},
 {"frame": 12, "location": [0, 0, 0.5], "scale": [1.2, 1.2, 0.8]},
 {"frame": 24, "location": [0, 0, 3], "scale": [1, 1, 1]}]
```

- `rotation_degrees` keeps full turns: keys at 0 and 360 spin once instead of standing
  still.
- `visible` keys hiding and showing (in the viewport and in renders).
- `data` keys a number on the object's light or camera, e.g. `{"energy": 0}` -> `{"energy": 800}`
  to fade a light in, or `{"lens": 35}` -> `{"lens": 85}` to zoom.
- Interpolation: `bezier` eases in and out (natural motion), `linear` keeps a constant
  speed (spins, conveyor belts), `constant` jumps (blinking, stop motion).
- The scene's frame range grows to include new keys; `clear_animation` removes all keys.
- Time: frames / fps seconds. 24 fps is film, 30 is video. A 2-second move at 24 fps
  spans 48 frames.

## Turntables

`create_turntable(target=["Product"], frames=120)` adds an empty at the subject's center
with a camera parented to it, spins the empty once, makes that camera the scene camera,
and sets the frame range so the loop is seamless. `mode="object"` spins one object in
place instead (it must use Euler rotation). Light the subject first: setup_lighting
orients its rig to the scene camera at the time you call it.

## Timeline

`set_timeline(frame_start=1, frame_end=96, fps=24)` sets the range and frame rate.
Changing the frame rate does not move keys, so motion gets faster or slower.

## Rendering

`render_animation(format="gif")` renders the frame range and returns a contact sheet of
evenly spaced frames: check it for motion that pops, objects leaving the frame, or lights
switching off.

- `mp4`: H.264 video, 960 px wide by default. Best for sharing.
- `gif`: loops everywhere, 320 px wide by default, 216 colors, at most 300 frames. Keep it
  short and small.
- `png`: one image per frame in a folder, for editing elsewhere.
- Cost: every frame is a full render. In background mode `engine="auto"` is Cycles on the
  CPU; keep `samples` at 8-16, `width` small, and use `frame_step=2` for drafts (playback
  keeps real-time speed). With the UI open, `engine="workbench"` is fastest but ignores
  your lights; use `eevee` for a quick lit preview.
- Files are written on the machine running Blender (`filepath`, or `mcp_renders/` next to
  the .blend, or the temp folder). The result gives the path, size, and duration.
