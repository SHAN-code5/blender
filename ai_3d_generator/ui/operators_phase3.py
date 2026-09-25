"""Phase 3 operators: prompt tools, image preview, real batch queue, export, and library."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.constants import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMEOUT
from ..core.errors import AI3DError, ValidationError
from ..services.batch_service import BatchQueue
from ..services.export_manager import export_objects
from ..services.library_service import AssetLibrary
from ..services.prompt_service import PromptEnhancer
from .operators import COORDINATOR, _scene_props
from .runtime_phase3 import library_for_props


class _BatchState:
    def __init__(self) -> None:
        self.queue: Optional[BatchQueue] = None
        self.active_job_id = ""
        self.timer: Optional[Any] = None
        self.active = False
        self.original_prompt = ""
        self.original_enhance_prompt = False
        self.original_enhanced_prompt = ""
        self.original_prompt_enhanced = False


BATCH = _BatchState()
_REGISTERED = False


class AI3D_OT_enhance_prompt(Operator):
    bl_idname = "ai3d.enhance_prompt"
    bl_label = "Enhance Prompt"
    bl_description = "Apply a local prompt template without generating a 3D asset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        try:
            result = PromptEnhancer(props.prompt_template).enhance(props.prompt)
            props.original_prompt = result.original
            props.enhanced_prompt = result.enhanced
            props.enhance_prompt = True
            props.prompt_enhanced = True
            props.status = "Prompt enhanced. Review or edit it before generation."
            return {'FINISHED'}
        except AI3DError as exc:
            self.report({'ERROR'}, exc.user_message())
            return {'CANCELLED'}


class AI3D_OT_load_reference(Operator):
    bl_idname = "ai3d.load_reference"
    bl_label = "Select Reference Image"
    bl_description = "Select and preview a PNG, JPG, JPEG, or WEBP reference"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(name="Reference Image", default="", subtype='FILE_PATH')
    filter_glob: StringProperty(default="*.png;*.jpg;*.jpeg;*.webp", options={'HIDDEN'})

    def invoke(self, context: Any, event: Any) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        try:
            from .runtime_phase3 import load_reference_preview
            image = load_reference_preview(self.filepath, context)
            props.image_path = image.filepath
            props.reference_image = image
            props.status = "Reference image ready."
            return {'FINISHED'}
        except (AI3DError, ValueError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class AI3D_OT_add_batch(Operator):
    bl_idname = "ai3d.add_batch"
    bl_label = "Generate Batch"
    bl_description = "Start a reliable sequential batch queue; one prompt per line"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        if BATCH.active or props.timer_running or COORDINATOR.job is not None:
            self.report({'WARNING'}, "Wait for the active generation or batch to finish.")
            return {'CANCELLED'}
        prompts = [line.strip() for line in str(props.batch_prompts or "").splitlines() if line.strip()]
        if not prompts:
            self.report({'ERROR'}, "Enter at least one batch prompt.")
            return {'CANCELLED'}
        queue = BatchQueue(1)
        for index, prompt in enumerate(prompts, start=1):
            queue.enqueue(f"batch-{index}", prompt)
        BATCH.queue = queue
        BATCH.active = True
        BATCH.active_job_id = ""
        BATCH.original_prompt = props.prompt
        BATCH.original_enhance_prompt = bool(props.enhance_prompt)
        BATCH.original_enhanced_prompt = props.enhanced_prompt
        BATCH.original_prompt_enhanced = bool(props.prompt_enhanced)
        props.batch_running = True
        props.batch_paused = False
        props.batch_current_job_id = ""
        props.status = f"Queued {len(prompts)} batch prompts."
        BATCH.timer = bpy.app.timers.register(_batch_tick, first_interval=0.1)
        return {'FINISHED'}


class AI3D_OT_batch_pause(Operator):
    bl_idname = "ai3d.batch_pause"
    bl_label = "Pause Batch"
    bl_description = "Pause before the next queued asset; the active asset may finish"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        if not BATCH.active or BATCH.queue is None:
            self.report({'WARNING'}, "No batch is active.")
            return {'CANCELLED'}
        BATCH.queue.pause()
        props.batch_paused = True
        props.status = "Batch paused. The active job is not interrupted."
        return {'FINISHED'}


class AI3D_OT_batch_resume(Operator):
    bl_idname = "ai3d.batch_resume"
    bl_label = "Resume Batch"
    bl_description = "Resume launching queued assets"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        if not BATCH.active or BATCH.queue is None:
            self.report({'WARNING'}, "No batch is active.")
            return {'CANCELLED'}
        BATCH.queue.resume()
        props.batch_paused = False
        props.status = "Batch resumed."
        if BATCH.timer is None:
            BATCH.timer = bpy.app.timers.register(_batch_tick, first_interval=0.1)
        return {'FINISHED'}


class AI3D_OT_batch_cancel(Operator):
    bl_idname = "ai3d.batch_cancel"
    bl_label = "Cancel Batch"
    bl_description = "Cancel queued jobs and request cancellation of the active job"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        if not BATCH.active or BATCH.queue is None:
            self.report({'WARNING'}, "No batch is active.")
            return {'CANCELLED'}
        for job_id in BATCH.queue.pending_job_ids():
            BATCH.queue.cancel(job_id)
        if BATCH.active_job_id:
            BATCH.queue.cancel(BATCH.active_job_id)
        if COORDINATOR.job is not None:
            COORDINATOR.job.cancel()
        _finish_batch(props, "Batch cancelled.")
        return {'FINISHED'}


class AI3D_OT_batch_retry(Operator):
    bl_idname = "ai3d.batch_retry"
    bl_label = "Retry Batch Job"
    bl_description = "Retry one failed or cancelled batch job"
    bl_options = {'REGISTER', 'UNDO'}

    job_id: StringProperty(name="Batch Job ID", default="")

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        if BATCH.queue is None or not self.job_id:
            self.report({'ERROR'}, "Batch job not found.")
            return {'CANCELLED'}
        try:
            BATCH.queue.retry(self.job_id)
        except AI3DError as exc:
            self.report({'ERROR'}, exc.user_message())
            return {'CANCELLED'}
        BATCH.active = True
        BATCH.queue.resume()
        props.batch_running = True
        props.batch_paused = False
        props.status = f"Retrying batch job {self.job_id}."
        if BATCH.timer is None:
            BATCH.timer = bpy.app.timers.register(_batch_tick, first_interval=0.1)
        return {'FINISHED'}


class AI3D_OT_batch_delete(Operator):
    bl_idname = "ai3d.batch_delete"
    bl_label = "Delete Batch Job"
    bl_description = "Delete one non-processing batch job"
    bl_options = {'REGISTER', 'UNDO'}

    job_id: StringProperty(name="Batch Job ID", default="")

    def execute(self, context: Any) -> set[str]:
        if BATCH.queue is None or not self.job_id:
            return {'CANCELLED'}
        try:
            BATCH.queue.remove(self.job_id)
        except AI3DError as exc:
            self.report({'ERROR'}, exc.user_message())
            return {'CANCELLED'}
        return {'FINISHED'}


class AI3D_OT_batch_move_up(Operator):
    bl_idname = "ai3d.batch_move_up"
    bl_label = "Move Batch Job Up"
    bl_description = "Move a queued job one position earlier"
    bl_options = {'REGISTER', 'UNDO'}

    job_id: StringProperty(name="Batch Job ID", default="")

    def execute(self, context: Any) -> set[str]:
        if BATCH.queue is None or not self.job_id:
            return {'CANCELLED'}
        pending = BATCH.queue.pending_job_ids()
        if self.job_id in pending and pending.index(self.job_id) > 0:
            BATCH.queue.reorder(self.job_id, pending.index(self.job_id) - 1)
        return {'FINISHED'}


class AI3D_OT_batch_move_down(Operator):
    bl_idname = "ai3d.batch_move_down"
    bl_label = "Move Batch Job Down"
    bl_description = "Move a queued job one position later"
    bl_options = {'REGISTER', 'UNDO'}

    job_id: StringProperty(name="Batch Job ID", default="")

    def execute(self, context: Any) -> set[str]:
        if BATCH.queue is None or not self.job_id:
            return {'CANCELLED'}
        pending = BATCH.queue.pending_job_ids()
        if self.job_id in pending and pending.index(self.job_id) < len(pending) - 1:
            BATCH.queue.reorder(self.job_id, pending.index(self.job_id) + 1)
        return {'FINISHED'}


class AI3D_OT_batch_clear(Operator):
    bl_idname = "ai3d.batch_clear"
    bl_label = "Clear Batch History"
    bl_description = "Clear the finished in-memory batch queue"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        if BATCH.active:
            self.report({'WARNING'}, "Cancel the active batch before clearing it.")
            return {'CANCELLED'}
        BATCH.queue = None
        return {'FINISHED'}


class AI3D_OT_export_asset(Operator):
    bl_idname = "ai3d.export_asset"
    bl_label = "Export Asset"
    bl_description = "Export imported generated objects without silently overwriting"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(name="Export File", default="", subtype='FILE_PATH')
    output_format: EnumProperty(name="Format", items=[("glb", "GLB", ""), ("gltf", "glTF", ""), ("obj", "OBJ", ""), ("fbx", "FBX", ""), ("stl", "STL", "")], default="glb")
    overwrite: BoolProperty(name="Overwrite", default=False)
    filter_glob: StringProperty(default="*.glb;*.gltf;*.obj;*.fbx;*.stl", options={'HIDDEN'})

    def invoke(self, context: Any, event: Any) -> set[str]:
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        source = Path(props.output_path or "")
        objects = [obj for obj in bpy.data.objects if obj.get("ai3d_generated", False) and source.is_file() and Path(str(obj.get("ai3d_source_file", ""))).resolve() == source.resolve()]
        try:
            target = export_objects(objects, self.filepath, self.output_format, overwrite=self.overwrite)
            props.last_export_path = str(target)
            props.status = f"Exported asset to {target.name}."
            return {'FINISHED'}
        except (AI3DError, ValueError, OSError) as exc:
            message = exc.user_message() if isinstance(exc, AI3DError) else str(exc)
            self.report({'ERROR'}, message)
            return {'CANCELLED'}


class AI3D_OT_favorite_asset(Operator):
    bl_idname = "ai3d.favorite_asset"
    bl_label = "Favorite Asset"
    bl_description = "Toggle a library asset favorite without changing its file"
    bl_options = {'REGISTER', 'UNDO'}

    asset_id: StringProperty(name="Asset ID", default="")

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        library = library_for_props(props, context)
        entry = next((item for item in library.list_assets() if item.id == self.asset_id), None)
        if entry is None:
            self.report({'ERROR'}, "Asset not found in the AI3D library.")
            return {'CANCELLED'}
        library.update(self.asset_id, favorite=not entry.favorite)
        return {'FINISHED'}


class AI3D_OT_import_library_asset(Operator):
    bl_idname = "ai3d.import_library_asset"
    bl_label = "Import Library Asset"
    bl_description = "Import a local library asset into Blender"
    bl_options = {'REGISTER', 'UNDO'}

    asset_id: StringProperty(name="Asset ID", default="")

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        library = library_for_props(props, context)
        entries = library.list_assets()
        entry = next((item for item in entries if item.id == self.asset_id), None)
        if entry is None:
            self.report({'ERROR'}, "The selected library asset file is unavailable.")
            return {'CANCELLED'}
        try:
            library.resolve_asset_path(entry.file)
            result = getattr(bpy.ops.ai3d, "import")(filepath=entry.file)
        except ValidationError:
            self.report({'ERROR'}, "The selected library asset file is unavailable or outside the library root.")
            return {'CANCELLED'}
        return {'FINISHED'} if 'FINISHED' in result else {'CANCELLED'}


class AI3D_OT_delete_library_asset(Operator):
    bl_idname = "ai3d.delete_library_asset"
    bl_label = "Remove Library Asset"
    bl_description = "Remove one library metadata record; generated files are preserved"
    bl_options = {'REGISTER', 'UNDO'}

    asset_id: StringProperty(name="Asset ID", default="")
    confirm: BoolProperty(name="Confirm", default=False)

    def execute(self, context: Any) -> set[str]:
        if not self.confirm:
            self.report({'WARNING'}, "Confirm removal to delete the library metadata row.")
            return {'CANCELLED'}
        if library_for_props(_scene_props(context), context).remove(self.asset_id):
            return {'FINISHED'}
        self.report({'ERROR'}, "Library asset not found.")
        return {'CANCELLED'}


class AI3D_OT_rename_library_asset(Operator):
    bl_idname = "ai3d.rename_library_asset"
    bl_label = "Rename Library Asset"
    bl_description = "Rename one library asset"
    bl_options = {'REGISTER', 'UNDO'}

    asset_id: StringProperty(name="Asset ID", default="")
    name: StringProperty(name="Name", default="")

    def execute(self, context: Any) -> set[str]:
        name = self.name.strip()
        if not name:
            self.report({'ERROR'}, "Asset name cannot be empty.")
            return {'CANCELLED'}
        if library_for_props(_scene_props(context), context).update(self.asset_id, name=name) is None:
            self.report({'ERROR'}, "Library asset not found.")
            return {'CANCELLED'}
        return {'FINISHED'}


class AI3D_OT_regenerate_library_asset(Operator):
    bl_idname = "ai3d.regenerate_library_asset"
    bl_label = "Regenerate"
    bl_description = "Load a library asset prompt into Create and start generation"
    bl_options = {'REGISTER', 'UNDO'}

    asset_id: StringProperty(name="Asset ID", default="")

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        library = library_for_props(props, context)
        entries = library.list_assets()
        entry = next((item for item in entries if item.id == self.asset_id), None)
        if entry is None:
            self.report({'ERROR'}, "Library asset not found.")
            return {'CANCELLED'}
        props.prompt = entry.prompt
        props.enhance_prompt = False
        props.enhanced_prompt = ""
        return bpy.ops.ai3d.generate()


class _BatchTerminalState(Operator):
    bl_idname = "ai3d._batch_terminal_state"
    bl_label = "Batch State"
    bl_options = {'INTERNAL'}

    def execute(self, context: Any) -> set[str]:
        return {'FINISHED'}


def _batch_tick() -> Optional[float]:
    if not BATCH.active or BATCH.queue is None:
        BATCH.timer = None
        return None
    props = _scene_props(bpy.context)
    if BATCH.queue.paused:
        return 0.5
    if props.timer_running or COORDINATOR.job is not None:
        return 0.2
    job_id = BATCH.queue.claim_next()
    if job_id is None:
        _finish_batch(props, "Batch completed.")
        return None
    item = BATCH.queue.items[job_id]
    BATCH.active_job_id = job_id
    props.batch_current_job_id = job_id
    props.prompt = item.prompt
    props.original_prompt = item.prompt
    props.enhance_prompt = False
    props.enhanced_prompt = ""
    props.prompt_enhanced = False
    result = bpy.ops.ai3d.generate()
    BATCH.timer = None
    if result != {'FINISHED'}:
        BATCH.queue.fail(job_id, "Generation operator did not start.")
        props.batch_current_job_id = ""
        BATCH.active_job_id = ""
        BATCH.timer = bpy.app.timers.register(_batch_tick, first_interval=0.2)
    return None


def on_generation_finished(context: Any, result: Any) -> None:
    """Advance the Blender batch only after the legacy coordinator is cleared."""
    if not BATCH.active or BATCH.queue is None or not BATCH.active_job_id:
        return
    props = _scene_props(context)
    job_id = BATCH.active_job_id
    if result.status == STATUS_COMPLETED:
        BATCH.queue.complete(job_id, {"file_path": result.file_path})
    elif result.status == STATUS_CANCELLED:
        BATCH.queue.cancel(job_id)
    else:
        BATCH.queue.fail(job_id, result.error or result.message or "Generation failed.")
    BATCH.active_job_id = ""
    props.batch_current_job_id = ""
    states = {item.state for item in BATCH.queue.snapshot()}
    if states.issubset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMEOUT}):
        _finish_batch(props, "Batch completed with failed or cancelled jobs." if STATUS_FAILED in states else "Batch completed.")
        return
    BATCH.timer = bpy.app.timers.register(_batch_tick, first_interval=0.25)


def _finish_batch(props: Any, message: str) -> None:
    if BATCH.timer is not None:
        try:
            bpy.app.timers.unregister(BATCH.timer)
        except (RuntimeError, ValueError):
            pass
        BATCH.timer = None
    BATCH.active = False
    BATCH.active_job_id = ""
    if BATCH.queue is not None:
        BATCH.queue.resume()
    props.batch_running = False
    props.batch_paused = False
    props.batch_current_job_id = ""
    props.prompt = BATCH.original_prompt
    props.enhance_prompt = BATCH.original_enhance_prompt
    props.enhanced_prompt = BATCH.original_enhanced_prompt
    props.prompt_enhanced = BATCH.original_prompt_enhanced
    props.status = message


classes = (
    AI3D_OT_enhance_prompt,
    AI3D_OT_load_reference,
    AI3D_OT_add_batch,
    AI3D_OT_batch_pause,
    AI3D_OT_batch_resume,
    AI3D_OT_batch_cancel,
    AI3D_OT_batch_retry,
    AI3D_OT_batch_delete,
    AI3D_OT_batch_move_up,
    AI3D_OT_batch_move_down,
    AI3D_OT_batch_clear,
    AI3D_OT_export_asset,
    AI3D_OT_favorite_asset,
    AI3D_OT_import_library_asset,
    AI3D_OT_delete_library_asset,
    AI3D_OT_rename_library_asset,
    AI3D_OT_regenerate_library_asset,
    _BatchTerminalState,
)


def register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    registered: list[Any] = []
    try:
        for cls in classes:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
                registered.append(cls)
    except Exception:
        for cls in reversed(registered):
            if getattr(cls, "is_registered", False):
                bpy.utils.unregister_class(cls)
        _REGISTERED = False
        raise
    _REGISTERED = True


def unregister() -> None:
    global _REGISTERED
    if BATCH.timer is not None:
        try:
            bpy.app.timers.unregister(BATCH.timer)
        except (RuntimeError, ValueError):
            pass
        BATCH.timer = None
    BATCH.active = False
    for cls in reversed(classes):
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
    _REGISTERED = False
