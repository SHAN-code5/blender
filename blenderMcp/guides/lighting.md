# Lighting, cameras, and rendering

## Lighting

- Fastest realistic light: an HDRI environment.
  `search_assets(source="polyhaven", asset_type="hdris", query="studio")`, then
  `import_asset(..., strength=1, rotation_degrees=...)`. Rotate to move the sun or
  windows.
- Sun: `create_object(kind="light_sun", energy=3–5, rotation_degrees=[45, 0, 30])`. Only the
  rotation matters for a sun.
- Studio three-point light with area lights (`kind="light_area"`, energy in watts):
  key 400–1000 W in front-left and above, fill 100–300 W front-right, rim 300–600 W behind.
  Point each one at the subject (see the camera snippet below; lights aim the same way).
- Point lights: 100–1000 W for room lamps. Energy falls off with distance squared, so a
  light twice as far needs four times the power.

## Cameras

`create_object(kind="camera", location=[...], lens=50)`; the first camera becomes the scene
camera (`make_active_camera=true` switches to a later one). 35 mm is wide, 50 mm natural,
85 mm and above for portraits and products.

Aim a camera (or light) at a point with `execute_blender_code`:

```python
from mathutils import Vector
cam = D.objects["Camera"]
target = Vector((0, 0, 1))
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
```

## Previews

- `render_views(views=["front", "right", "top", "iso"])` returns one image with several
  auto-framed angles. Use it to check proportions and placement.
- `render_image(view="camera")` renders through the scene camera (or an auto-framed iso
  view when there is none). `view` can also be front, back, left, right, top, or iso.
- `engine="auto"` uses Workbench (fast, flat shading) when Blender's UI is open and Cycles
  on the CPU in background mode. EEVEE and Workbench need a GPU; in background mode they
  are refused unless Blender was started with `BLENDER_MCP_GPU=1`.
- Raise `samples` (16 by default) only for final images; previews at 8–16 are fine.
- `viewport_screenshot` shows exactly what the user sees in the 3D View (UI mode only).

Render settings, the scene camera, and temporary preview cameras and lights are restored
or removed after every preview.
