"""Deterministic offline provider for UI and integration tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.capabilities import ProviderCapabilities
from ..core.constants import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED, STATUS_PROCESSING, STATUS_QUEUED
from ..core.errors import ValidationError
from ..core.models import JobHandle, JobStatus, ProviderConfig
from .image_to_3d import ImageTo3DProvider


class MockProvider(ImageTo3DProvider):
    """Simulate queued → processing → completed/failed without a network call."""

    identifier = "mock"
    display_name = "Mock Provider (offline)"

    def __init__(self, config: Optional[ProviderConfig] = None, client: Any = None) -> None:
        super().__init__(config or ProviderConfig(), client)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._ticks: Dict[str, int] = {}

    def create_generation_job(self, request: Dict[str, Any]) -> JobHandle:
        mode = str(request.get("generation_mode", "text_to_3d"))
        if mode not in {"text_to_3d", "image_to_3d"}:
            raise ValidationError("Unsupported generation mode.")
        prompt = str(request.get("prompt", "")).strip()
        if mode == "image_to_3d":
            from ..utils.images import validate_image_path
            validate_image_path(request.get("reference_image_path") or request.get("image_path", ""))
        elif not prompt:
            raise ValidationError("Prompt cannot be empty.")
        job_id = "mock-" + hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        self._jobs[job_id] = {"request": dict(request), "failed": "mock-fail" in prompt.lower()}
        self._ticks[job_id] = 0
        return JobHandle(job_id=job_id, provider=self.identifier, raw={"job_id": job_id})

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.identifier,
            name=self.display_name,
            supported_generation_modes=("text_to_3d", "image_to_3d"),
            supported_formats=("glb",),
            supports_text_to_3d=True,
            supports_image_to_3d=True,
            supports_cancellation=True,
        )

    def create_image_job(self, image_path: str, request: Dict[str, Any]) -> JobHandle:
        """Mock the same upload-capable image contract for offline integration."""
        payload = dict(request)
        payload["reference_image_path"] = image_path
        payload["image_path"] = image_path
        return self.create_generation_job(payload)

    def get_job_status(self, job_id: str, elapsed_seconds: float = 0.0) -> JobStatus:
        if job_id not in self._jobs:
            return JobStatus(state=STATUS_FAILED, error="Mock job was not found.", raw={})
        job = self._jobs[job_id]
        if job.get("cancelled"):
            return JobStatus(state=STATUS_CANCELLED, progress=self._ticks[job_id] / 3.0, raw=job)
        self._ticks[job_id] += 1
        tick = self._ticks[job_id]
        job = self._jobs[job_id]
        if tick == 1:
            return JobStatus(state=STATUS_QUEUED, progress=0.0, raw=job)
        if tick == 2:
            return JobStatus(state=STATUS_PROCESSING, progress=0.5, message="Generating mock asset", raw=job)
        if job["failed"]:
            return JobStatus(state=STATUS_FAILED, progress=1.0, error="Mock provider was configured to fail.", raw=job)
        return JobStatus(
            state=STATUS_COMPLETED,
            progress=1.0,
            output_url="https://mock.invalid/assets/mock-asset.glb",
            raw=job,
        )

    def cancel_job(self, job_id: str) -> None:
        if job_id in self._jobs:
            self._jobs[job_id]["cancelled"] = True

    def download_asset(self, url: str, destination: Any) -> str:
        # The service-level downloader normally handles a URL. Mock jobs use
        # a local fixture URL to make the offline path explicit and testable.
        if not str(url).startswith("https://mock.invalid/"):
            raise ValidationError("Mock provider can only download its fixture URL.")
        target = Path(destination)
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "mock_asset.glb"
        if not fixture.exists():
            raise ValidationError("Mock asset fixture is missing from the extension package.")
        target.parent.mkdir(parents=True, exist_ok=True)
        from ..utils.files import atomic_write_bytes
        atomic_write_bytes(target, fixture.read_bytes())
        return str(target)
