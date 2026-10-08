# Blender Python through execute_blender_code

`bpy`, `C` (`bpy.context`), and `D` (`bpy.data`) are already available. Printed text comes
back as `stdout`; assign a JSON-serializable value to `result` to return it.

```python
result = [(o.name, o.type) for o in C.scene.objects if o.type == "MESH"]
```

## Rules that avoid most errors

- Prefer `bpy.data` and `bmesh` over `bpy.ops`. Many operators need a specific editor,
  mode, or selection and fail with "context is incorrect" when called from a script.
- Do not leave Blender in Edit Mode. If you enter it, switch back with
  `bpy.ops.object.mode_set(mode="OBJECT")`.
- Look objects up by name each time (`D.objects["Name"]`); names change when duplicates
  get `.001`.
- After changing geometry or transforms, call `C.view_layer.update()` before reading
  `dimensions`, `bound_box`, or `matrix_world`.

## A custom mesh with bmesh

```python
import bmesh
mesh = D.meshes.new("Wedge")
bm = bmesh.new()
verts = [bm.verts.new(v) for v in [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1)]]
for face in [(0, 1, 2, 3), (0, 4, 5, 3), (0, 1, 4), (3, 2, 5), (1, 2, 5, 4)]:
    bm.faces.new([verts[i] for i in face])
bm.normal_update()
bm.to_mesh(mesh)
bm.free()
obj = D.objects.new("Wedge", mesh)
C.scene.collection.objects.link(obj)
```

## Differences between Blender versions

| Topic | Blender 3.x | Blender 4.x | Blender 5.x |
|---|---|---|---|
| Principled BSDF sockets | `Emission`, `Specular`, `Transmission`, `Subsurface`, `Clearcoat`, `Sheen` | `Emission Color`, `Specular IOR Level`, `Transmission Weight`, `Subsurface Weight`, `Coat Weight`, `Sheen Weight` | as 4.x |
| EEVEE engine id | `BLENDER_EEVEE` | `BLENDER_EEVEE` (4.0–4.1), `BLENDER_EEVEE_NEXT` (4.2–4.5) | `BLENDER_EEVEE` |
| Auto smooth | `mesh.use_auto_smooth` | removed in 4.1; use `bpy.ops.object.shade_auto_smooth()` or a "Smooth by Angle" modifier | as 4.1+ |
| Material/world nodes | set `use_nodes = True` | set `use_nodes = True` | new materials and worlds already have node trees; `Material.use_nodes` is deprecated |
| Geometry nodes sockets | `tree.inputs.new(...)` | `tree.interface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")` | as 4.x |
| OBJ/STL/PLY import | `wm.obj_import` (3.2+); `import_mesh.stl` and `import_mesh.ply` in older releases | `wm.obj_import`, `wm.stl_import`, `wm.ply_import` | as 4.x |

Look sockets up by name with a fallback when a script must run on several versions:

```python
bsdf = next(n for n in D.materials["Glass"].node_tree.nodes if n.type == "BSDF_PRINCIPLED")
socket = bsdf.inputs.get("Transmission Weight") or bsdf.inputs.get("Transmission")
socket.default_value = 1.0
```

`import_model`, `export_scene`, and `render_image` already pick the right operator or engine
for the running version.

## Safe mode

When the server runs with `--safe-mode`, scripts may not import `os`, `subprocess`,
`socket` and similar modules, call `open`/`exec`/`eval`, or register handlers, timers, or
classes. Use `import_model`, `export_scene`, `save_blend_file`, and `render_image` for file
access instead.
