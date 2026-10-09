# Lighting, cameras, and rendering

## Lighting

- Quickest good result: `setup_lighting(preset=...)`. It sizes the lights to the subject
  and turns the rig toward the scene camera, so set the camera first.
  - `three_point`: key, fill, and rim area lights on a dark background. Good default.
  - `studio`: softer and brighter, light-gray background. Products and sculptures.
  - `outdoor`: a sun plus a gradient sky with a ground below the horizon.
  - `dramatic`: a warm hard key from the side, a cool rim, near-black background.
  `strength` scales every light, and `azimuth_degrees` turns the rig. Calling it again
  replaces the previous rig. The result lists `other_lights`, so you can hide existing
  lights with `modify_object(visible=false)` if the scene gets too bright.
- Most realistic: an HDRI environment.
  `search_assets(source="polyhaven", asset_type="hdris", query="studio")`, then
  `import_asset(..., hdri_strength=1, hdri_rotation=...)`. setup_lighting keeps an HDRI
  world unless you pass `world=true`.
- One light at a time: `set_light(kind="area", location=[...], target="Subject", energy=500)`.
  Call it again with the same `name` to change energy, color, `temperature_kelvin`,
  `size` (softness), or aim. `track=true` keeps it pointed at a moving object.
- Power: area/point/spot lights are in watts and fall off with distance squared, so a
  light twice as far needs four times the power. Around 500-2000 W at 3-5 m lights a
  person-sized subject. A sun's energy is 3-5 for daylight, and only its rotation matters.
- Color temperature: 2700 K warm bulb, 4000 K neutral, 5500 K daylight, 8000 K shade.

## Cameras

- `set_camera(view="iso", fit=["Chair", "Table"])` places the camera so those objects fill
  the frame from that side (`front`, `back`, `left`, `right`, `top`, `iso`). Without `fit`
  it frames all visible geometry. It becomes the scene camera.
- `set_camera(location=[6, -6, 3], target="Chair")` aims at an object's center or a point;
  `track=true` keeps aiming as things move (useful in animations).
- `lens`: 24 mm wide, 35 mm reportage, 50 mm natural, 85-135 mm portraits and products.
- Depth of field: `focus="Chair"` (or a distance in meters) and `fstop=2` for a soft
  background; `depth_of_field=false` turns it off.
- `resolution=[1920, 1080]` sets the render size and aspect ratio. Frame after changing it.

## Previews

- `render_views(views=["front", "right", "top", "iso"])` returns one image with several
  auto-framed angles. Use it to check proportions and placement.
- `render_image(view="camera")` renders through the scene camera (or an auto-framed iso
  view when there is none). `view` can also be front, back, left, right, top, or iso.
- `engine="auto"` uses Workbench (fast, flat shading) when Blender's UI is open and Cycles
  on the CPU in background mode. Workbench ignores lights; use `engine="cycles"` or
  `"eevee"` to judge lighting. EEVEE and Workbench need a GPU; in background mode they
  are refused unless Blender was started with `BLENDER_MCP_GPU=1`.
- Raise `samples` (16 by default) only for final images; previews at 8-16 are fine.
- `viewport_screenshot` shows exactly what the user sees in the 3D View (UI mode only).

Render settings, the scene camera, and temporary preview cameras and lights are restored
or removed after every preview.
