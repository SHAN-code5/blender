"""Optional local Blender thumbnail service.

The renderer is deliberately best-effort: every temporary camera and render
setting is restored in ``finally`` so a failed preview cannot alter the user's
scene. Callers may continue when no thumbnail is produced.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence


class ThumbnailService:
    def __init__(self, context: Any = None) -> None:
        self.context = context

    def create_preview(
        self,
        objects: Sequence[Any],
        output_path: str | Path,
        width: int = 256,
        height: int = 256,
    ) -> Optional[str]:
        """Render a small preview, returning its path or ``None`` on failure."""
        if self.context is None:
            return None
        meshes = [obj for obj in objects if getattr(obj, "type", "") == "MESH"]
        if not meshes:
            return None
        target = Path(output_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        camera = None
        camera_data = None
        scene = None
        old_settings = None
        old_camera = None
        try:
            import bpy

            scene = self.context.scene
            old_camera = scene.camera
            old_settings = (
                scene.render.resolution_x,
                scene.render.resolution_y,
                scene.render.resolution_percentage,
                scene.render.filepath,
                scene.render.image_settings.file_format,
            )
            camera_data = bpy.data.cameras.new("AI3D_PreviewCamera")
            camera = bpy.data.objects.new("AI3D_PreviewCamera", camera_data)
            scene.collection.objects.link(camera)
            scene.camera = camera
            max_dimension = max(obj.dimensions.length for obj in meshes)
            camera.location = (0.0, -max(4.0, max_dimension * 2.0), 0.0)
            camera.rotation_euler = (1.5708, 0.0, 0.0)
            scene.render.resolution_x = width
            scene.render.resolution_y = height
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "PNG"
            scene.render.filepath = str(target)
            bpy.ops.render.render(write_still=True)
            return str(target) if target.exists() else None
        except Exception:
            return None
        finally:
            try:
                if scene is not None and old_settings is not None:
                    scene.camera = old_camera
                    (
                        scene.render.resolution_x,
                        scene.render.resolution_y,
                        scene.render.resolution_percentage,
                        scene.render.filepath,
                        scene.render.image_settings.file_format,
                    ) = old_settings
                if camera is not None:
                    bpy.data.objects.remove(camera, do_unlink=True)
                if camera_data is not None:
                    bpy.data.cameras.remove(camera_data)
            except Exception:
                pass
