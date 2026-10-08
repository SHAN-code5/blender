# Materials

`set_material(object_name, ...)` creates or updates a Principled BSDF material and puts it
in the object's first material slot (`append=true` adds a slot instead).

## Colors

- `'#RRGGBB'` strings are sRGB, the same as colors on the web or in a color picker.
- `[r, g, b]` lists are linear 0–1 values, the same as Blender's own color fields.
  Linear values look darker than the same numbers in sRGB; prefer hex when matching a
  described color.

## Starting values

| Surface | color | metallic | roughness |
|---|---|---|---|
| Polished metal (chrome) | `#d8d8d8` | 1 | 0.05–0.15 |
| Brushed steel | `#b4b4b4` | 1 | 0.3–0.45 |
| Gold | `#ffc65c` | 1 | 0.2–0.3 |
| Copper | `#e08860` | 1 | 0.25–0.35 |
| Glossy plastic | any | 0 | 0.2–0.35 |
| Matte plastic | any | 0 | 0.5–0.7 |
| Painted wall | `#e8e4dc` | 0 | 0.8–0.9 |
| Rubber | `#202020` | 0 | 0.8–0.95 |
| Wood (plain) | `#8b5a2b` | 0 | 0.55–0.7 |
| Ceramic | white | 0 | 0.1–0.2 |

Glowing parts (screens, lamps): `emission_color` plus `emission_strength` 2–10. Emission
lights Cycles and EEVEE scenes, not Workbench.

Transparent glass needs the transmission input, which differs by version. Use
`execute_blender_code` and look the socket up by name: `Transmission Weight` in Blender
4.0+, `Transmission` before.

## Textures

For realistic surfaces, use a PBR texture set:
`import_asset(source="polyhaven", asset_id=..., asset_type="textures", apply_to=["Wall"])`.
It builds a material with color, roughness, metallic, normal, and displacement maps.
`uv_scale` repeats the texture (2 = twice as dense). Check `dimensions_mm` in the search
result: a texture made for a 2 m wall needs `uv_scale` near the wall size divided by 2.

Textures follow the object's UV map. Primitives from `create_object` have one; for meshes
without UVs the material falls back to box projection and skips the normal map.
