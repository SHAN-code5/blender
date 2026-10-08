"""Text- and image-to-3D generation for the ``generate_3d`` tool.

Jobs run in the MCP server process on ai3dgenerator's job manager (submit,
poll, size-bounded download, file validation), each in a background thread so
a long generation never blocks the server or trips a client timeout. Blender
only imports the finished file.
"""

import json
import mimetypes
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ai3dgenerator.core.capabilities import ProviderCapabilities
from ai3dgenerator.core.constants import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
)
from ai3dgenerator.core.errors import ProviderResponseError, ValidationError
from ai3dgenerator.core.models import JobHandle, JobStatus, ProviderConfig
from ai3dgenerator.providers.customRest import CustomRESTProvider
from ai3dgenerator.providers.imageTo3d import ImageTo3DProvider
from ai3dgenerator.providers.mock import MockProvider
from ai3dgenerator.services.downloadManager import DownloadManager
from ai3dgenerator.services.jobManager import JobManager

from . import net

PROVIDERS = ("tripo", "hyper3d", "custom_api", "mock")


class GenerationError(RuntimeError):
    """Generation could not start or was asked about an unknown job."""


def _image_upload(image_path: str):
    path = Path(image_path)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path.name, path.read_bytes(), mime


def _clamp_progress(value: Any, scale: float = 100.0) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0) / scale))
    except (TypeError, ValueError):
        return 0.0


class TripoProvider(ImageTo3DProvider):
    """Tripo3D OpenAPI v2 (https://platform.tripo3d.ai)."""

    identifier = "tripo"
    display_name = "Tripo"
    requires_api_key = True
    API = "https://api.tripo3d.ai/v2/openapi"
    STATES = {
        "queued": STATUS_QUEUED, "running": STATUS_PROCESSING, "unknown": STATUS_PROCESSING,
        "success": STATUS_COMPLETED, "failed": STATUS_FAILED, "banned": STATUS_FAILED,
        "expired": STATUS_FAILED, "cancelled": STATUS_CANCELLED,
    }

    def __init__(self, api_key: str, api: str = API, fetch=net.request_json,
                 model_version: Optional[str] = None) -> None:
        super().__init__(ProviderConfig(base_url=api, api_key=api_key or "", timeout=60.0))
        self.api = api.rstrip("/")
        self._fetch = fetch
        self.model_version = model_version

    def _call(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        try:
            data = self._fetch(method, f"{self.api}/{path}",
                               headers={"Authorization": f"Bearer {self.config.api_key}"}, **kwargs)
        except net.NetError as exc:
            raise ProviderResponseError(f"Tripo request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("Tripo returned an unexpected response.")
        if data.get("code") not in (0, None):
            raise ProviderResponseError(f"Tripo error {data.get('code')}: {data.get('message') or 'unknown'}")
        return data.get("data") or {}

    def _task(self, body: Dict[str, Any]) -> JobHandle:
        if self.model_version:
            body["model_version"] = self.model_version
        data = self._call("POST", "task", json_body=body)
        task_id = data.get("task_id")
        if not task_id:
            raise ProviderResponseError("Tripo did not return a task id.")
        return JobHandle(job_id=str(task_id), provider=self.identifier, raw=data)

    def create_generation_job(self, request: Dict[str, Any]) -> JobHandle:
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("Prompt cannot be empty.")
        body: Dict[str, Any] = {"type": "text_to_model", "prompt": prompt}
        if request.get("negative_prompt"):
            body["negative_prompt"] = str(request["negative_prompt"])
        return self._task(body)

    def create_image_job(self, image_path: str, request: Dict[str, Any]) -> JobHandle:
        name, payload, mime = _image_upload(image_path)
        body, content_type = net.encode_multipart([], [("file", name, payload, mime)])
        uploaded = self._call("POST", "upload", data=body, content_type=content_type)
        token = uploaded.get("image_token") or uploaded.get("file_token")
        if not token:
            raise ProviderResponseError("Tripo did not return an upload token for the image.")
        file_type = {"image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
        return self._task({"type": "image_to_model", "file": {"type": file_type, "file_token": token}})

    def get_job_status(self, job_id: str, elapsed_seconds: float = 0.0) -> JobStatus:
        data = self._call("GET", f"task/{urllib.parse.quote(job_id, safe='')}")
        raw_state = str(data.get("status") or "").lower()
        state = self.STATES.get(raw_state, STATUS_PROCESSING)
        output = data.get("output") or {}
        url = output.get("pbr_model") or output.get("model") or output.get("base_model")
        if state == STATUS_COMPLETED and not url:
            raise ProviderResponseError("Tripo finished without a model URL.")
        return JobStatus(state=state, progress=_clamp_progress(data.get("progress")),
                         output_url=url if state == STATUS_COMPLETED else None,
                         error=f"Tripo task {raw_state}" if state == STATUS_FAILED else None, raw=data)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(provider_id=self.identifier, name=self.display_name,
                                    supported_generation_modes=("text_to_3d", "imageTo3d"),
                                    supported_formats=("glb",), supports_text_to_3d=True,
                                    supports_imageTo3d=True)


class RodinProvider(ImageTo3DProvider):
    """Hyper3D Rodin API v2 (https://hyper3d.ai)."""

    identifier = "hyper3d"
    display_name = "Hyper3D Rodin"
    requires_api_key = True
    API = "https://hyperhuman.deemos.com/api/v2"

    def __init__(self, api_key: str, api: str = API, fetch=net.request_json, tier: str = "Regular") -> None:
        super().__init__(ProviderConfig(base_url=api, api_key=api_key or "", timeout=60.0))
        self.api = api.rstrip("/")
        self._fetch = fetch
        self.tier = tier
        self._subscriptions: Dict[str, str] = {}

    def _post(self, path: str, **kwargs) -> Dict[str, Any]:
        try:
            data = self._fetch("POST", f"{self.api}/{path}",
                               headers={"Authorization": f"Bearer {self.config.api_key}"}, **kwargs)
        except net.NetError as exc:
            raise ProviderResponseError(f"Hyper3D request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderResponseError("Hyper3D returned an unexpected response.")
        if data.get("error"):
            raise ProviderResponseError(f"Hyper3D error: {data['error']}")
        return data

    def _submit(self, fields_: List, files: List) -> JobHandle:
        body, content_type = net.encode_multipart(fields_, files)
        data = self._post("rodin", data=body, content_type=content_type)
        task_uuid = data.get("uuid")
        key = (data.get("jobs") or {}).get("subscription_key")
        if not task_uuid or not key:
            raise ProviderResponseError(f"Hyper3D did not return a job: {data.get('message') or data}")
        self._subscriptions[task_uuid] = key
        return JobHandle(job_id=task_uuid, provider=self.identifier, raw=data)

    def create_generation_job(self, request: Dict[str, Any]) -> JobHandle:
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValidationError("Prompt cannot be empty.")
        return self._submit([("prompt", prompt), ("tier", self.tier), ("geometry_file_format", "glb")], [])

    def create_image_job(self, image_path: str, request: Dict[str, Any]) -> JobHandle:
        fields_ = [("tier", self.tier), ("geometry_file_format", "glb")]
        prompt = str(request.get("prompt") or "").strip()
        if prompt:
            fields_.append(("prompt", prompt))
        name, payload, mime = _image_upload(image_path)
        return self._submit(fields_, [("images", name, payload, mime)])

    def get_job_status(self, job_id: str, elapsed_seconds: float = 0.0) -> JobStatus:
        key = self._subscriptions.get(job_id)
        if not key:
            return JobStatus(state=STATUS_FAILED, error="Unknown Hyper3D job.")
        data = self._post("status", json_body={"subscription_key": key})
        statuses = [str(job.get("status") or "") for job in data.get("jobs") or []]
        if not statuses:
            return JobStatus(state=STATUS_QUEUED, raw=data)
        if any(s.lower() == "failed" for s in statuses):
            return JobStatus(state=STATUS_FAILED, error="Hyper3D reported a failed job.", raw=data)
        done = sum(1 for s in statuses if s.lower() == "done")
        if done < len(statuses):
            generating = any(s.lower() == "generating" for s in statuses)
            return JobStatus(state=STATUS_PROCESSING if generating or done else STATUS_QUEUED,
                             progress=done / float(len(statuses)), raw=data)
        files = self._post("download", json_body={"task_uuid": job_id}).get("list") or []
        model = next((f for f in files if str(f.get("name", "")).lower().endswith(".glb")), None)
        if not model or not model.get("url"):
            raise ProviderResponseError("Hyper3D finished without a GLB download.")
        return JobStatus(state=STATUS_COMPLETED, progress=1.0, output_url=model["url"], raw=data)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(provider_id=self.identifier, name=self.display_name,
                                    supported_generation_modes=("text_to_3d", "imageTo3d"),
                                    supported_formats=("glb",), supports_text_to_3d=True,
                                    supports_imageTo3d=True)


def load_custom_config(path: str) -> ProviderConfig:
    """Read a ProviderConfig for the generic REST provider from a JSON file."""
    try:
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GenerationError(f"cannot read the generation config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GenerationError(f"{path} must contain a JSON object")
    known = {f.name for f in fields(ProviderConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise GenerationError(f"unknown keys in {path}: {', '.join(unknown)}; valid: {', '.join(sorted(known))}")
    return ProviderConfig(**data)


@dataclass
class GenerationJob:
    job_id: str
    provider: str
    prompt: str
    manager: JobManager
    started: float = field(default_factory=time.monotonic)
    thread: Optional[threading.Thread] = None
    import_options: Dict[str, Any] = field(default_factory=dict)
    imported: Optional[Dict[str, Any]] = None
    import_error: Optional[str] = None
    import_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def finished(self) -> bool:
        return self.manager.snapshot.is_finished

    def status(self) -> Dict[str, Any]:
        snapshot = self.manager.snapshot
        return {
            "job_id": self.job_id,
            "provider": self.provider,
            "state": snapshot.state,
            "progress": round(snapshot.progress, 3),
            "message": snapshot.message,
            "error": snapshot.error or None,
            "elapsed_seconds": round(time.monotonic() - self.started, 1),
        }


class Generator:
    """Creates providers from settings and tracks running generation jobs."""

    def __init__(self, cache_root: Path, tripo_api_key: Optional[str] = None,
                 hyper3d_api_key: Optional[str] = None, custom_config: Optional[str] = None,
                 poll_interval: float = 3.0, sleep: Callable[[float], None] = time.sleep,
                 factories: Optional[Dict[str, Callable[[], Any]]] = None) -> None:
        self.cache_root = Path(cache_root)
        self.tripo_api_key = tripo_api_key
        self.hyper3d_api_key = hyper3d_api_key
        self.custom_config = custom_config
        self.poll_interval = poll_interval
        self._sleep = sleep
        self._factories = factories or {}
        self._jobs: Dict[str, GenerationJob] = {}
        self._lock = threading.Lock()

    def configured(self) -> List[str]:
        names = []
        if self.tripo_api_key or "tripo" in self._factories:
            names.append("tripo")
        if self.hyper3d_api_key or "hyper3d" in self._factories:
            names.append("hyper3d")
        if self.custom_config or "custom_api" in self._factories:
            names.append("custom_api")
        return names

    def default_provider(self) -> str:
        configured = self.configured()
        if not configured:
            raise GenerationError(
                "No 3D generator is configured. Set BLENDER_MCP_TRIPO_API_KEY or BLENDER_MCP_HYPER3D_API_KEY, "
                "or point BLENDER_MCP_GENERATION_CONFIG at a custom REST provider config. "
                "provider='mock' tests the pipeline offline (it always returns a cube).")
        return configured[0]

    def make_provider(self, name: str):
        if name in self._factories:
            return self._factories[name]()
        if name == "mock":
            return MockProvider()
        if name == "tripo":
            if not self.tripo_api_key:
                raise GenerationError("Tripo needs BLENDER_MCP_TRIPO_API_KEY (platform.tripo3d.ai).")
            return TripoProvider(self.tripo_api_key)
        if name == "hyper3d":
            if not self.hyper3d_api_key:
                raise GenerationError("Hyper3D Rodin needs BLENDER_MCP_HYPER3D_API_KEY (hyper3d.ai).")
            return RodinProvider(self.hyper3d_api_key)
        if name == "custom_api":
            if not self.custom_config:
                raise GenerationError("custom_api needs BLENDER_MCP_GENERATION_CONFIG pointing at a JSON "
                                      "provider config (base_url, generate_path, status_path, ...).")
            return CustomRESTProvider(load_custom_config(self.custom_config))
        raise GenerationError(f"unknown provider {name!r}; choose from {', '.join(PROVIDERS)}")

    def start(self, provider_name: Optional[str], prompt: str, image_path: Optional[str] = None,
              negative_prompt: Optional[str] = None, timeout_seconds: float = 900.0,
              import_options: Optional[Dict[str, Any]] = None) -> GenerationJob:
        name = provider_name or self.default_provider()
        provider = self.make_provider(name)
        request: Dict[str, Any] = {"prompt": prompt or "", "output_format": "glb",
                                   "generation_mode": "imageTo3d" if image_path else "text_to_3d"}
        if negative_prompt:
            request["negative_prompt"] = negative_prompt
        if image_path:
            request["image_path"] = request["reference_image_path"] = str(Path(image_path).expanduser())
        downloads = DownloadManager(self.cache_root / "generated", timeout=120.0, max_size_mb=512)
        manager = JobManager(provider, downloads, job_timeout=timeout_seconds)
        manager.start(request)
        job = GenerationJob(job_id=uuid.uuid4().hex[:12], provider=name, prompt=prompt or "",
                            manager=manager, import_options=dict(import_options or {}))
        interval = 0.1 if name == "mock" else self.poll_interval
        job.thread = threading.Thread(target=self._run, args=(job, interval), name=f"generate-{job.job_id}",
                                      daemon=True)
        with self._lock:
            self._jobs[job.job_id] = job
        job.thread.start()
        return job

    def _run(self, job: GenerationJob, interval: float) -> None:
        manager = job.manager
        while not manager.snapshot.is_finished:
            manager.tick()
            if not manager.snapshot.is_finished:
                self._sleep(interval)

    def get(self, job_id: str) -> GenerationJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise GenerationError(f"no generation job {job_id!r} in this server session")
        return job

    def output_path(self, job: GenerationJob) -> Path:
        snapshot = job.manager.snapshot
        if snapshot.state != STATUS_COMPLETED or not snapshot.output_path:
            raise GenerationError(f"job {job.job_id} has no finished model ({snapshot.state})")
        return Path(snapshot.output_path)
