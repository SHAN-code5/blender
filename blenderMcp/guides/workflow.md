# Working in Blender through MCP

## Start every task the same way

1. `get_bridge_status`: is Blender connected, which version, UI or background mode.
2. `get_scene_info`: what already exists. Use the exact object names it returns.
3. Plan the scene in real-world sizes, then build it with the structured tools.
4. Look at the result with `render_views` (several angles in one image) or `render_image`,
   fix what looks wrong, and look again.
5. `save_blend_file` before large or destructive changes. With Blender's UI open, `undo`
   reverts the last steps.

## Space and units

- Units are meters. Z is up. The front view looks along +Y, so "in front" is -Y and
  "right" is +X.
- Rotations are degrees in XYZ order. `[0, 0, 90]` turns an object a quarter turn
  counter-clockwise when seen from above.
- Primitives are centered on their origin. A 2 m cube at `location [0, 0, 1]` sits on the
  floor; in general put an object's center at half its height.
- Blender renames duplicates (`Chair`, `Chair.001`). Always use the name a tool returns.

## Real-world sizes

| Thing | Size |
|---|---|
| Adult | 1.7–1.8 m tall |
| Door | 2.0 × 0.9 m |
| Table | 0.75 m high, 1.6 × 0.9 m top |
| Chair | seat 0.45 m high, back 0.9 m |
| Sofa | 0.85 m high, 2.0 × 0.9 m |
| Room ceiling | 2.5–3.0 m |
| Car | 4.5 × 1.8 × 1.5 m |
| Tree | 4–15 m |

`import_asset` and `generate_3d` take `target_size` (largest side in meters) and
`location` (where the bottom center goes), so imported models arrive at the right size
standing on the floor.

## Organizing

- Put related objects in a collection (`collection` argument) such as `Furniture` or
  `Lights`.
- Parent parts to an empty with `modify_object(parent=...)` to move them together.
- Keep geometry light: subdivision level 1–2, bevel segments 2–3.
