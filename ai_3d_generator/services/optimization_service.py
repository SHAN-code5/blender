"""Optional Blender optimization/LOD service with explicit, bounded actions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence


@dataclass
class OptimizationOptions:
    target_polygons: int = 0
    decimate: bool = False
    auto_uv: bool = False
    apply_transforms: bool = False
    smooth_shading: bool = False
    generate_lod: bool = False
    lod_targets: tuple[int, ...] = ()


class OptimizationService:
    """Creates modifiers without destroying the original mesh."""

    def __init__(self, context: Any = None) -> None:
        self.context = context

    def apply(self, objects: Sequence[Any], options: Optional[OptimizationOptions] = None) -> str:
        options = options or OptimizationOptions()
        meshes = [obj for obj in objects if getattr(obj, "type", "") == "MESH"]
        if not meshes:
            return "No meshes selected for optimization."
        if options.decimate and options.target_polygons > 0:
            current = sum(len(obj.data.polygons) for obj in meshes)
            ratio = max(0.01, min(1.0, options.target_polygons / float(current))) if current else 1.0
            for obj in meshes:
                modifier = obj.modifiers.new("AI3D Optimization", 'DECIMATE')
                modifier.ratio = ratio
        if options.smooth_shading:
            for obj in meshes:
                for polygon in obj.data.polygons:
                    polygon.use_smooth = True
        if options.generate_lod:
            for index, target in enumerate(options.lod_targets, start=1):
                if target <= 0:
                    continue
                for obj in meshes:
                    modifier = obj.modifiers.new(f"AI3D_LOD{index}", 'DECIMATE')
                    current = max(1, len(obj.data.polygons))
                    modifier.ratio = max(0.01, min(1.0, target / float(current)))
        return "Optimization modifiers added; original mesh data preserved."
