# Modeling

## Primitives

`create_object(kind=...)` makes `cube`, `uv_sphere`, `ico_sphere`, `cylinder`, `cone`,
`torus`, `plane`, `circle`, `grid`, `monkey`, plus `empty`, `text`, `camera`, and lights.
`size` is the overall size in meters (default 2, like Blender). Every mesh gets a UV map.

Shape a primitive into an object with `modify_object`:

- `dimensions=[2, 0.9, 0.05]` sets the exact size (a table top), adjusting scale.
- `scale` multiplies the size; `rotation_degrees` and `location` place it.
- `parent="Table"` attaches it to another object and keeps its world position.

A table from primitives: a cube with `dimensions [1.6, 0.9, 0.05]` at `[0, 0, 0.75]`, and four
cubes with `dimensions [0.06, 0.06, 0.72]` at `[±0.72, ±0.37, 0.36]`.

## Modifiers

`add_modifier(object_name, modifier_type, settings)`. Unknown or invalid settings are
reported back instead of failing the call. Object and collection settings take names.

| Type | Useful settings | Use for |
|---|---|---|
| `BEVEL` | `width` (m), `segments` | Soften hard edges; almost every man-made object |
| `SUBSURF` | `levels`, `render_levels` | Smooth organic shapes (1–2 levels) |
| `SOLIDIFY` | `thickness` | Give planes thickness (walls, cloth, leaves) |
| `ARRAY` | `count`, `relative_offset_displace` (e.g. `[1.1, 0, 0]`) | Rows of repeated parts (fence posts, stairs) |
| `MIRROR` | `use_axis` (e.g. `[true, false, false]`), `mirror_object` | Symmetric objects |
| `BOOLEAN` | `operation` (`DIFFERENCE`, `UNION`, `INTERSECT`), `object` | Cut holes (windows, doors) |
| `DECIMATE` | `ratio` | Reduce polygons on heavy imports |
| `WEIGHTED_NORMAL` | — | Cleaner shading on beveled hard surfaces |
| `DISPLACE` | `strength` | Push geometry along normals |

After a `BOOLEAN`, hide the cutter: `modify_object(name="Cutter", visible=false)`.

## Beyond primitives

Use `execute_blender_code` with `bmesh` for custom meshes, or geometry nodes for procedural
work. See the `python` guide for API details that differ between Blender versions.
