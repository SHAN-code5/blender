# Asset libraries and AI generation

Search first, then import by id. Downloads happen in the MCP server process (Blender stays
responsive), are cached, and reach a remote Blender over the bridge when needed.

## Sources

| Source | What | Key | License |
|---|---|---|---|
| `polyhaven` | Thousands of HDRIs, PBR textures, and models | none | CC0, no credit needed |
| `sketchfab` | A large catalog of user-uploaded models (downloadable ones only) | `BLENDER_MCP_SKETCHFAB_API_KEY` for import | Per model; many need credit |
| `polypizza` | About 10,000 low-poly models | `BLENDER_MCP_POLYPIZZA_API_KEY` | CC0 or CC-BY (CC-BY needs credit) |

## Searching

```text
search_assets(source="polyhaven", query="red brick wall", asset_type="textures")
search_assets(source="polyhaven", asset_type="hdris", list_categories=true)
search_assets(source="sketchfab", query="vintage car")
search_assets(source="polypizza", query="tree", licence="CC0")
```

Poly Haven search understands meaning ("couch" finds sofas). Results include ids,
authors, licenses, and thumbnail URLs.

## Importing

- HDRI: `import_asset(source="polyhaven", asset_id=..., asset_type="hdris", strength=1)` makes a
  new world and packs the image into the .blend. The previous world gets a fake user, so it
  survives saving and you can switch back to it.
- Texture: `import_asset(..., asset_type="textures", apply_to=["Floor"], uv_scale=2)` builds a
  PBR material and assigns it.
- Model: `import_asset(..., target_size=1.2, location=[2, 0, 0])` scales the model so its
  largest side is 1.2 m and stands it at that spot. Poly Haven models come from the
  artist's .blend (best materials), falling back to glTF when the running Blender is too
  old to read it.
- `resolution` for Poly Haven: `1k` (default, fast), `2k`, `4k`. Each step is about four
  times the download size.

Every imported object, material, or world records its origin in custom properties
(`mcp_source`, `mcp_asset_id`, `mcp_url`, `mcp_license`, `mcp_author`, `mcp_credit`), so
credits survive in the saved .blend. Show the `credit` text to the user for CC-BY models.

## Generating models

`generate_3d(prompt="a weathered wooden treasure chest")` creates a model with the
configured generator and imports it. Pass `image_path` (a PNG/JPG on the server's machine)
for image-to-3D.

| Provider | Setup |
|---|---|
| `tripo` | `BLENDER_MCP_TRIPO_API_KEY` |
| `hyper3d` | `BLENDER_MCP_HYPER3D_API_KEY` (Hyper3D Rodin) |
| `custom_api` | `BLENDER_MCP_GENERATION_CONFIG` pointing at a JSON REST provider config |
| `mock` | nothing; returns a cube, for testing the pipeline |

Generation takes one to several minutes. `generate_3d` waits up to `wait_seconds`; if the
job is still running it returns a `job_id`. Call `get_generation_status(job_id)` until it
finishes; the model is imported once, on the call that sees it complete.
