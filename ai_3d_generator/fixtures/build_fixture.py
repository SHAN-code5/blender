"""Minimal valid GLB fixture used by the offline Mock Provider.

The file contains a 1x1x1 cube mesh with a basic material. It is intentionally
small and self-contained so the mock path tests real import validation.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path


def _build_glb() -> bytes:
    positions = [
        -0.5, -0.5, -0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, -0.5,
        -0.5, -0.5, 0.5, 0.5, -0.5, 0.5, 0.5, 0.5, 0.5, -0.5, 0.5, 0.5,
    ]
    indices = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7, 0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5, 0, 3, 2, 0, 2, 4, 5, 6, 7, 5, 7, 4]
    position_bytes = struct.pack("<24f", *positions)
    index_bytes = struct.pack("<36H", *indices)
    while len(index_bytes) % 4:
        index_bytes += b"\x00"
    bin_data = struct.pack("<I", len(position_bytes)) + position_bytes + struct.pack("<I", len(index_bytes)) + index_bytes
    while len(bin_data) % 4:
        bin_data += b"\x00"
    gltf = {
        "asset": {"version": "2.0", "generator": "AI3D offline fixture"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "MockCube"}],
        "meshes": [{"name": "MockCubeMesh", "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"name": "MockMaterial", "pbrMetallicRoughness": {"baseColorFactor": [0.35, 0.18, 0.08, 1.0], "metallicFactor": 0.0, "roughnessFactor": 0.65}}],
        "buffers": [{"byteLength": len(bin_data)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(position_bytes) + 8, "byteLength": len(index_bytes), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5]},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR", "min": [0], "max": [7]},
        ],
    }
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(json_bytes)) + b"JSON" + json_bytes + struct.pack("<I", len(bin_data)) + b"BIN\0" + bin_data


if __name__ == "__main__":
    target = Path(__file__).with_name("mock_asset.glb")
    target.write_bytes(_build_glb())
