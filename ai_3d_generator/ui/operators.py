"""Blender operators and the single timer-driven job coordinator.

The coordinator is module state, not a worker thread. Blender's timer callback
updates the scene property and then optionally imports the downloaded asset on
the main thread. This avoids the unsafe pattern of touching bpy.data from a
network thread.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import Operator

from ..core.config import validate_config
from ..core.constants import STATUS_COMPLETED
from ..core.errors import AI3DError, ImportError_
from ..core.logging import get_logger
from ..core.models import GenerationRequest, ProviderConfig
from ..providers.registry import PROVIDERS, get_provider_class
from ..services.history import make_entry, read_history, visible_history_index_to_storage, write_history
from ..services.prompt_service import effective_prompt
from ..services.job_manager import JobManager, JobSnapshot
from ..services.download_manager import DownloadManager
from ..utils.files import ensure_directory
from .runtime import _default_cache, get_preferences, provider_config_from_preferences
from .runtime_phase3 import load_reference_preview, provider_capabilities
from ..utils.validation import safe_provider_message


class _Coordinator:
    def __init__(self) -> None:
        self.job: Optional[JobManager] = None
        self.timer: Optional[Any] = None
        self.request: Optional[Dict[str, Any]] = None
        self.log_path: Optional[Path] = None
        self.finished: bool = False


COORDINATOR = _Coordinator()


def _scene_props(context: Any) -> Any:
    return context.scene.ai3d


def _set_history_row(row: Any, item: Any) -> None:
    """Copy dataclass values into Blender properties using their native types."""
    for key, value in item.to_dict().items():
        if not hasattr(row, key):
            continue
        if isinstance(value, bool):
            setattr(row, key, value)
        elif isinstance(value, int):
            setattr(row, key, int(value))
        elif isinstance(value, float):
            setattr(row, key, float(value))
        else:
            setattr(row, key, str(value))


def _load_history_into_props(context: Any) -> None:
    props = _scene_props(context)
    rows = read_history(_history_path(props, context))
    props.history.clear()
    for item in rows[-20:]:
        row = props.history.add()
        _set_history_row(row, item)


def _cache_dir(props: Any, context: Any = None) -> Path:
    configured = str(getattr(props, "cache_dir", "") or "").strip()
    if configured:
        return ensure_directory(configured)
    return ensure_directory(_default_cache(props, context))


def _history_path(props: Any, context: Any = None) -> Path:
    return _cache_dir(props, context) / "history.json"


def _request_from_props(props: Any) -> GenerationRequest:
    prompt = effective_prompt(props.prompt, props.enhanced_prompt, props.enhance_prompt)
    if props.generation_mode == "image_to_3d":
        from ..utils.images import validate_image_path
        validate_image_path(props.image_path)
    return GenerationRequest(
        prompt=prompt,
        negative_prompt=props.negative_prompt.strip(),
        quality=props.quality,
        output_format=props.output_format.lower(),
        style=props.style.strip() or "realistic",
        polygon_target=props.polygon_target,
        generate_materials=props.generate_materials,
        generate_textures=props.generate_textures,
        topology_preference=props.topology_preference,
        texture_resolution=props.texture_resolution,
        auto_uv=props.auto_uv,
        prompt_enhanced=props.prompt_enhanced and props.enhance_prompt,
        original_prompt=getattr(props, "original_prompt", ""),
        generation_mode=props.generation_mode,
        image_path=props.image_path,
        reference_image_path=props.image_path,
    )


def _runtime_config(context: Any, props: Any) -> ProviderConfig:
    return provider_config_from_preferences(context, props)


def _provider_url_from_context(context: Any, props: Any) -> str:
    """Return the provider URL from Preferences, never from Scene state."""
    prefs = get_preferences(context)
    if prefs is not None:
        return str(getattr(prefs, "api_base_url", "") or "").strip()
    if str(getattr(props, "provider", "mock") or "mock") == "mock":
        return "http://127.0.0.1:8000"
    return ""


def _config_values_for_request(request: GenerationRequest, props: Any, context: Any = None) -> dict[str, Any]:
    values = request.to_dict()
    provider_url = _provider_url_from_context(context, props)
    if not provider_url and str(getattr(props, "provider", "mock")) == "mock":
        provider_url = "http://127.0.0.1:8000"
    values.update({
        "provider": props.provider,
        "base_url": provider_url,
        "timeout": props.timeout,
        "poll_interval": props.poll_interval,
        "job_timeout": props.job_timeout,
    })
    return values


def _validate_props(props: Any, context: Any = None) -> None:
    request = _request_from_props(props)
    errors = validate_config(_config_values_for_request(request, props, context))
    if errors:
        raise AI3DError("; ".join(errors))


def _import_downloaded(context: Any, path: str, *, force: bool = False) -> Any:
    """Import a downloaded asset; explicit user imports bypass the auto toggle."""
    props = _scene_props(context)
    if not path or (not force and not props.auto_import):
        return
    try:
        from ..services.import_manager import ImportManager, ImportOptions
    except Exception as exc:
        raise AI3DError("The completed asset could not be prepared for Blender import.", detail=str(exc)) from exc

    options = ImportOptions(
        location_mode=props.location_mode,
        auto_center=props.auto_center,
        auto_scale=props.auto_scale,
        smooth_shading=props.smooth_shading,
        recalculate_normals=True,
    )
    import_result = ImportManager(context).import_asset(path, Path(path).suffix, options)
    if import_result is not None:
        props.status = "3D asset generated and imported successfully."
    return import_result


def _add_history(context: Any, job_id: str, request: GenerationRequest, file_path: str, status: str, error: str = "") -> None:
    props = _scene_props(context)
    path = _history_path(props, context)
    rows = read_history(path)
    entry = make_entry(
        job_id=job_id,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        provider=props.provider,
        model=props.model,
        quality=props.quality,
        output_format=request.output_format,
        file_path=file_path,
        status=status,
        error=safe_provider_message(error, "") if error else "",
        generation_mode=request.generation_mode,
        image_path=request.image_path,
        style=request.style,
        polygon_target=request.polygon_target,
        topology_preference=request.topology_preference,
        texture_resolution=request.texture_resolution,
        auto_uv=request.auto_uv,
        generate_materials=request.generate_materials,
        generate_textures=request.generate_textures,
        prompt_enhanced=request.prompt_enhanced,
        original_prompt=request.original_prompt,
    )
    rows.append(entry)
    write_history(path, rows)
    props.history.clear()
    for item in rows[-20:]:
        row = props.history.add()
        _set_history_row(row, item)


class AI3D_OT_generate(Operator):
    bl_idname = "ai3d.generate"
    bl_label = "Generate 3D"
    bl_description = "Submit the current prompt to the selected provider"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        if props.timer_running:
            self.report({'WARNING'}, "A generation job is already running.")
            return {'CANCELLED'}
        try:
            _validate_props(props, context)
            request = _request_from_props(props)
            provider_cls = get_provider_class(props.provider)
            provider_url = _provider_url_from_context(context, props)
            # The offline mock adapter never needs a configured URL.
            if not provider_url and props.provider == "mock":
                provider_url = "http://127.0.0.1:8000"
            config = _runtime_config(context, props)
            if not str(config.base_url or "").strip() and str(getattr(props, "provider", "mock") or "mock") == "mock":
                config.base_url = provider_url
            capabilities = provider_capabilities(props.provider, config)
            requested_mode = props.generation_mode
            if not provider_url:
                raise AI3DError("Configure the provider URL in Preferences before generating.")
            if provider_url != str(config.base_url or "").strip():
                raise AI3DError("The runtime provider configuration does not match Preferences. Reopen Preferences and save the provider URL.")
            if requested_mode not in capabilities.supported_generation_modes or not capabilities.supports_generation_mode(requested_mode):
                raise AI3DError("The selected provider does not support this generation mode.")
            if props.output_format.lower() not in capabilities.supported_formats_for_mode(requested_mode):
                raise AI3DError("The selected provider does not support the requested output format.")
            if requested_mode == "image_to_3d":
                if not props.image_path:
                    self.report({'ERROR'}, "Select a reference image first.")
                    return {'CANCELLED'}
                load_reference_preview(props.image_path, context)
            provider = provider_cls(config)
            manager = DownloadManager(
                _cache_dir(props, context),
                timeout=props.timeout,
                max_size_mb=props.max_asset_size_mb,
                allowed_hosts=config.download_host_allowlist,
            )
            COORDINATOR.request = request.to_dict()
            COORDINATOR.finished = False
            COORDINATOR.log_path = _cache_dir(props, context) / "logs" / "ai3d.log"
            get_logger(COORDINATOR.log_path, debug=props.debug_mode)
            COORDINATOR.job = JobManager(
                provider,
                manager,
                job_timeout=props.job_timeout,
                on_update=lambda snapshot: _on_update(context, snapshot),
                on_finished=lambda result, snapshot: _on_finished(context, result, snapshot),
            )
            COORDINATOR.job.poll_interval = max(0.1, float(props.poll_interval or 1.0))
            COORDINATOR.job.start(COORDINATOR.request)
            props.timer_running = not COORDINATOR.job.snapshot.is_finished
            if props.timer_running:
                props.status = COORDINATOR.job.snapshot.message
                COORDINATOR.timer = bpy.app.timers.register(_timer_callback, first_interval=props.poll_interval)
            return {'FINISHED'}
        except (AI3DError, ValueError, OSError) as exc:
            message = exc.user_message() if isinstance(exc, AI3DError) else str(exc)
            props.status = "Generation failed."
            props.error_message = message
            self.report({'ERROR'}, message)
            return {'CANCELLED'}


class AI3D_OT_cancel(Operator):
    bl_idname = "ai3d.cancel"
    bl_label = "Cancel Generation"
    bl_description = "Cancel the active provider job"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        if COORDINATOR.job:
            COORDINATOR.job.cancel()
        return {'FINISHED'}


class AI3D_OT_retry(Operator):
    bl_idname = "ai3d.retry"
    bl_label = "Retry Generation"
    bl_description = "Retry the selected history entry using its saved prompt"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(name="Index", default=-1)

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        _load_history_into_props(context)
        index = self.index
        if index < 0 or index >= len(props.history):
            self.report({'ERROR'}, "Select a history entry to retry.")
            return {'CANCELLED'}
        item = props.history[index]
        provider_id = getattr(item, "provider", "")
        if provider_id not in PROVIDERS:
            self.report({'ERROR'}, f"The recorded provider '{provider_id}' is no longer available.")
            return {'CANCELLED'}
        props.prompt = item.prompt
        props.negative_prompt = getattr(item, "negative_prompt", "")
        props.provider = provider_id
        props.model = item.model
        props.quality = item.quality
        props.output_format = item.output_format.lower()
        props.generation_mode = getattr(item, "generation_mode", "text_to_3d")
        props.image_path = getattr(item, "image_path", "")
        props.style = getattr(item, "style", "realistic")
        props.polygon_target = int(getattr(item, "polygon_target", 50000))
        props.topology_preference = getattr(item, "topology_preference", "balanced")
        props.texture_resolution = int(getattr(item, "texture_resolution", 2048))
        props.auto_uv = bool(getattr(item, "auto_uv", False))
        props.generate_materials = bool(getattr(item, "generate_materials", True))
        props.generate_textures = bool(getattr(item, "generate_textures", True))
        props.prompt_enhanced = bool(getattr(item, "prompt_enhanced", False))
        props.original_prompt = getattr(item, "original_prompt", item.prompt)
        props.enhance_prompt = bool(getattr(item, "prompt_enhanced", False))
        props.enhanced_prompt = item.prompt if props.prompt_enhanced else ""
        if props.generation_mode == "image_to_3d" and props.image_path:
            try:
                props.reference_image = load_reference_preview(props.image_path, context)
            except (AI3DError, ValueError, OSError):
                props.reference_image = None
        return bpy.ops.ai3d.generate()


class AI3D_OT_import(Operator):
    bl_idname = "ai3d.import"
    bl_label = "Import Generated Asset"
    bl_description = "Import the last downloaded generated asset"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(name="File", default="", subtype='FILE_PATH')

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        path = self.filepath or props.output_path
        if not path:
            self.report({'ERROR'}, "Generate or choose an asset first.")
            return {'CANCELLED'}
        try:
            cache_root = _cache_dir(props, context).resolve()
            candidate = Path(path).expanduser().resolve()
            try:
                candidate.relative_to(cache_root)
            except ValueError:
                self.report({'ERROR'}, "The selected asset is outside the generated cache.")
                return {'CANCELLED'}
            if not candidate.is_file():
                self.report({'ERROR'}, "The selected generated asset no longer exists.")
                return {'CANCELLED'}
            _import_downloaded(context, str(candidate), force=True)
            props.status = "3D asset ready."
            return {'FINISHED'}
        except (AI3DError, ImportError_, ValueError) as exc:
            props.error_message = exc.user_message() if isinstance(exc, (AI3DError, ImportError_)) else str(exc)
            self.report({'ERROR'}, props.error_message)
            return {'CANCELLED'}


class AI3D_OT_refresh_history(Operator):
    bl_idname = "ai3d.refresh_history"
    bl_label = "Refresh History"
    bl_description = "Reload generation history from the local history file"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        _load_history_into_props(context)
        return {'FINISHED'}


class AI3D_OT_delete_history_entry(Operator):
    bl_idname = "ai3d.delete_history_entry"
    bl_label = "Delete History Entry"
    bl_description = "Delete one history entry without deleting its downloaded asset"
    bl_options = {'REGISTER'}

    index: IntProperty(name="Index", default=-1)

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        _load_history_into_props(context)
        path = _history_path(props, context)
        rows = read_history(path)
        if self.index < 0 or self.index >= len(rows):
            return {'CANCELLED'}
        try:
            storage_index = visible_history_index_to_storage(rows, self.index)
        except IndexError:
            return {'CANCELLED'}
        rows.pop(storage_index)
        write_history(path, rows)
        props.history.clear()
        for item in rows[-20:]:
            row = props.history.add()
            _set_history_row(row, item)
        return {'FINISHED'}


class AI3D_OT_clear_history(Operator):
    bl_idname = "ai3d.clear_history"
    bl_label = "Clear History"
    bl_description = "Delete local generation history without deleting generated files"
    bl_options = {'REGISTER'}

    confirm: BoolProperty(name="Confirm", default=False)

    def execute(self, context: Any) -> set[str]:
        props = _scene_props(context)
        props.history.clear()
        path = _history_path(props, context)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self.report({'ERROR'}, f"Could not clear history: {exc}")
            return {'CANCELLED'}
        return {'FINISHED'}


class AI3D_OT_open_settings(Operator):
    bl_idname = "ai3d.open_settings"
    bl_label = "Open Settings"
    bl_description = "Open the extension preferences page"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'PREFERENCES':
                    area.spaces.active.context = 'ADDONS'
                    area.tag_redraw()
                    return {'FINISHED'}
        try:
            bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
        except Exception:
            self.report({'INFO'}, "Open Edit → Preferences → Add-ons and select AI 3D Object Generator.")
        return {'FINISHED'}


class AI3D_OT_open_asset_folder(Operator):
    bl_idname = "ai3d.open_asset_folder"
    bl_label = "Open Asset Folder"
    bl_description = "Open the generated asset cache directory"
    bl_options = {'REGISTER'}

    def execute(self, context: Any) -> set[str]:
        path = Path(props_path(context)).parent
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                import subprocess

                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, str(path)], close_fds=True)
        except Exception as exc:
            self.report({'ERROR'}, f"Could not open folder: {exc}")
            return {'CANCELLED'}
        return {'FINISHED'}


class AI3D_OT_delete_generated(Operator):
    bl_idname = "ai3d.delete_generated"
    bl_label = "Delete Generated Objects"
    bl_description = "Delete only objects tagged by this extension"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context: Any) -> set[str]:
        from ..core.constants import GENERATED_COLLECTION_NAME

        collection = bpy.data.collections.get(GENERATED_COLLECTION_NAME)
        if collection is None:
            return {'FINISHED'}
        for obj in list(collection.all_objects):
            if obj.get("ai3d_generated", False):
                bpy.data.objects.remove(obj, do_unlink=True)
        return {'FINISHED'}


def props_path(context: Any) -> str:
    return str(_history_path(_scene_props(context), context))


def _record_completed_asset(context: Any, result: Any, request: GenerationRequest) -> None:
    """Record completed downloads in the bounded local library."""
    if not result.file_path or not Path(result.file_path).is_file():
        return
    props = _scene_props(context)
    try:
        from ..services.library_record_service import record_generated_asset
        from .runtime_phase3 import library_for_props

        record_generated_asset(
            library_for_props(props, context),
            file_path=result.file_path,
            job_id=result.job_id,
            prompt=request.prompt,
            provider=props.provider,
            model=props.model,
            output_format=request.output_format,
            name=request.prompt,
            copy_file=True,
        )
    except (OSError, ValueError) as exc:
        props.error_message = safe_provider_message(f"Asset downloaded, but library recording failed: {exc}", "Asset downloaded, but library recording failed.")


def _on_update(context: Any, snapshot: JobSnapshot) -> None:
    props = _scene_props(context)
    _set_runtime_state(props, snapshot)
    if context.area:
        context.area.tag_redraw()


def _on_finished(context: Any, result: Any, snapshot: JobSnapshot) -> None:
    if COORDINATOR.finished:
        return
    COORDINATOR.finished = True
    props = _scene_props(context)
    _set_runtime_state(props, snapshot, terminal=True)
    try:
        request = GenerationRequest.from_dict(COORDINATOR.request or {})
        if snapshot.state == STATUS_COMPLETED and snapshot.output_path:
            _import_downloaded(context, snapshot.output_path)
            _record_completed_asset(context, result, request)
        _add_history(
            context,
            snapshot.job_id,
            request,
            snapshot.output_path,
            snapshot.state,
            snapshot.error,
        )
        props.status = (
            "3D asset generated and imported successfully."
            if snapshot.state == STATUS_COMPLETED
            else f"Generation {snapshot.state}."
        )
        props.error_message = safe_provider_message(snapshot.error, "")
    except (AI3DError, OSError, ValueError) as exc:
        props.status = "Generation finished, but local history/library recording failed."
        props.error_message = safe_provider_message(exc.user_message() if isinstance(exc, AI3DError) else exc, "Generation finished, but local history/library recording failed.")
    finally:
        if COORDINATOR.timer is not None:
            try:
                bpy.app.timers.unregister(COORDINATOR.timer)
            except (RuntimeError, ValueError):
                pass
            COORDINATOR.timer = None
        COORDINATOR.job = None
        COORDINATOR.request = None
    try:
        from .operators_phase3 import on_generation_finished
        on_generation_finished(context, result)
    except Exception as exc:
        props.status = "Generation finished, but batch state could not be updated."
        props.error_message = safe_provider_message(exc, "Generation finished, but batch state could not be updated.")


def _result_from_snapshot(job: Optional[JobManager]) -> Any:
    from ..core.models import GenerationResult

    snapshot = job.snapshot if job is not None else JobSnapshot()
    return GenerationResult(
        job_id=snapshot.job_id,
        status=snapshot.state,
        message=snapshot.message,
        error=snapshot.error,
        file_path=snapshot.output_path,
    )


def _set_runtime_state(props: Any, snapshot: JobSnapshot, *, terminal: bool = False) -> None:
    props.status = safe_provider_message(snapshot.message, "Generation status updated.")
    props.progress = snapshot.progress
    props.current_job_id = snapshot.job_id
    props.error_message = safe_provider_message(snapshot.error, "")
    props.output_path = snapshot.output_path
    if terminal:
        props.timer_running = False


def _timer_callback() -> Optional[float]:
    if COORDINATOR.job is None:
        return None
    COORDINATOR.job.tick()
    if COORDINATOR.job is None or COORDINATOR.job.snapshot.is_finished:
        return None
    return max(0.1, float(getattr(COORDINATOR.job, "poll_interval", 1.0)))


classes = (
    AI3D_OT_generate,
    AI3D_OT_cancel,
    AI3D_OT_retry,
    AI3D_OT_import,
    AI3D_OT_refresh_history,
    AI3D_OT_delete_history_entry,
    AI3D_OT_clear_history,
    AI3D_OT_open_settings,
    AI3D_OT_open_asset_folder,
    AI3D_OT_delete_generated,
)


def register() -> None:
    registered: list[Any] = []
    try:
        for cls in classes:
            if not getattr(cls, "is_registered", False):
                bpy.utils.register_class(cls)
                registered.append(cls)
    except Exception:
        for cls in reversed(registered):
            _safe_unregister_class(cls)
        raise


def _safe_unregister_class(cls: Any) -> None:
    if getattr(cls, "is_registered", False):
        bpy.utils.unregister_class(cls)


def unregister() -> None:
    if COORDINATOR.timer is not None:
        try:
            bpy.app.timers.unregister(COORDINATOR.timer)
        except Exception:
            pass
        COORDINATOR.timer = None
    COORDINATOR.job = None
    for cls in reversed(classes):
        _safe_unregister_class(cls)
