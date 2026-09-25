"""Timer-driven generation job state machine.

Submission, polling, and download each run from a bounded timer callback. The
UI operator only initializes the state machine, so a slow provider POST does
not block Blender's interface.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ..core.constants import (
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_QUEUED,
    STATUS_SUBMITTING,
    STATUS_TIMEOUT,
)
from ..core.errors import AI3DError
from ..core.models import GenerationResult, JobHandle
from .download_manager import DownloadedAsset, DownloadManager


@dataclass
class JobSnapshot:
    state: str = STATUS_QUEUED
    progress: float = 0.0
    message: str = "Preparing generation..."
    job_id: str = ""
    error: str = ""
    output_path: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_finished(self) -> bool:
        return self.state in {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMEOUT}


def _safe_persist_text(value: str, fallback: str = "") -> str:
    from ..utils.validation import safe_provider_message
    return safe_provider_message(value, fallback)


class JobManager:
    """Own one provider job from submission through optional asset download."""

    def __init__(
        self,
        provider: Any,
        download_manager: DownloadManager,
        *,
        job_timeout: float = 900.0,
        on_update: Optional[Callable[[JobSnapshot], None]] = None,
        on_finished: Optional[Callable[[GenerationResult, JobSnapshot], None]] = None,
    ) -> None:
        self.provider = provider
        self.download_manager = download_manager
        job_timeout_value = float(job_timeout)
        if job_timeout_value != job_timeout_value or job_timeout_value in {float("inf"), float("-inf")} or not 1.0 <= job_timeout_value <= 86400.0:
            raise ValueError("job_timeout must be between 1 and 86400 seconds")
        self.job_timeout = job_timeout_value
        self._poll_interval = 1.0
        self.on_update = on_update
        self.on_finished = on_finished
        self.snapshot = JobSnapshot()
        self.handle: Optional[JobHandle] = None
        self.downloaded: Optional[DownloadedAsset] = None
        self._started = 0.0
        self._cancel_requested = False
        self._phase = "new"
        self._request: Dict[str, Any] = {}
        self._output_url = ""
        self._output_format = ""
        self._finished_notified = False

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    @poll_interval.setter
    def poll_interval(self, value: float) -> None:
        numeric = float(value)
        if numeric != numeric or numeric in {float("inf"), float("-inf")} or not 0.1 <= numeric <= 120.0:
            raise ValueError("poll_interval must be between 0.1 and 120 seconds")
        self._poll_interval = numeric

    def start(self, request: Dict[str, Any]) -> None:
        """Initialize a pending job without performing network I/O."""
        self._started = time.monotonic()
        self._phase = "submit"
        self._request = dict(request)
        self._cancel_requested = False
        self._finished_notified = False
        self.snapshot = JobSnapshot(
            state=STATUS_SUBMITTING,
            progress=0.02,
            message="Preparing generation request...",
            raw=dict(request),
        )
        self._update(self.snapshot.state, self.snapshot.progress, self.snapshot.message)

    def tick(self) -> None:
        """Advance one state-machine phase; safe to call from bpy.app.timers."""
        if self.snapshot.is_finished:
            return
        if self._cancel_requested:
            self._finish_cancelled()
            return
        elapsed = time.monotonic() - self._started
        if elapsed > self.job_timeout:
            self._finish_error(STATUS_TIMEOUT, "Generation timed out before the provider completed the job.", "timeout")
            return
        try:
            if self._phase == "submit":
                self._submit()
            elif self._phase == "poll":
                self._poll(elapsed)
            elif self._phase == "download":
                self._download()
                result = GenerationResult(
                    job_id=self.snapshot.job_id,
                    status=STATUS_COMPLETED,
                    file_path=self.snapshot.output_path,
                    message=self.snapshot.message,
                )
                self._notify_finished(result)
            else:
                raise AI3DError("Generation job was not initialized.")
        except AI3DError as exc:
            self._finish_error(STATUS_FAILED, exc.user_message(), exc.technical_message())
        except Exception as exc:
            self._finish_error(STATUS_FAILED, "Generation failed unexpectedly.", str(exc))

    def _submit(self) -> None:
        self._update(STATUS_SUBMITTING, 0.03, "Submitting request...")
        self.provider.validate_credentials()
        mode = str(self._request.get("generation_mode", "text_to_3d"))
        if mode == "image_to_3d":
            from ..providers.image_to_3d import ImageTo3DProvider
            from ..utils.images import validate_image_path
            if not isinstance(self.provider, ImageTo3DProvider):
                raise AI3DError("The selected provider does not support Image → 3D.")
            image_path = validate_image_path(self._request.get("reference_image_path") or self._request.get("image_path", ""))
            self.handle = self.provider.create_image_job(image_path, self._request)
        else:
            self.handle = self.provider.create_generation_job(self._request)
        if not self.handle or not self.handle.job_id:
            raise AI3DError("The provider did not return a valid job ID.")
        self.snapshot.job_id = self.handle.job_id
        self._phase = "poll"
        self._update(STATUS_QUEUED, 0.05, "Generation queued...")

    def _poll(self, elapsed: float) -> None:
        assert self.handle is not None
        status = self.provider.get_job_status(self.handle.job_id, elapsed)
        if status.state == STATUS_QUEUED:
            self._update(STATUS_QUEUED, max(0.05, min(0.2, status.progress)), status.message or "Generation queued...")
        elif status.state == STATUS_PROCESSING:
            self._update(STATUS_PROCESSING, max(0.2, min(0.85, 0.2 + status.progress * 0.65)), status.message or "Generating 3D asset...")
        elif status.state == STATUS_COMPLETED:
            if not status.output_url:
                raise AI3DError("Generation completed, but no asset URL was returned.")
            self._output_url = status.output_url
            self._output_format = str(self._request.get("output_format", "") or "")
            self._phase = "download"
            self._update(STATUS_PROCESSING, 0.9, "Downloading asset...")
        elif status.state == STATUS_CANCELLED:
            self._finish_cancelled()
        elif status.state == STATUS_FAILED:
            self._finish_error(STATUS_FAILED, "The provider reported a failed generation.", status.error)
        elif status.state == STATUS_TIMEOUT:
            self._finish_error(STATUS_TIMEOUT, "The provider job timed out.", status.error)
        else:
            raise AI3DError("The provider returned an unknown job status.")

    def _download(self) -> None:
        if not self._output_url:
            raise AI3DError("Generation completed, but no asset URL was returned.")
        self._update(STATUS_PROCESSING, 0.92, "Downloading asset...")
        downloaded = self.download_manager.download(
            self._output_url,
            self.handle.job_id if self.handle else "job",
            self._output_format,
            self.provider,
        )
        self.downloaded = downloaded
        self._phase = "done"
        self._update(STATUS_COMPLETED, 1.0, "3D asset generated and downloaded successfully.", output_path=str(downloaded.path))

    def cancel(self) -> None:
        """Request cancellation; provider cleanup is best-effort."""
        if self.snapshot.is_finished:
            return
        self._cancel_requested = True
        self._update(STATUS_CANCELLING, self.snapshot.progress, "Cancelling generation...")
        if self.handle is not None:
            try:
                self.provider.cancel_job(self.handle.job_id)
            except Exception:
                pass

    def _update(self, state: str, progress: float, message: str, output_path: str = "") -> None:
        self.snapshot.state = state
        self.snapshot.progress = max(0.0, min(1.0, float(progress)))
        self.snapshot.message = message
        if output_path:
            self.snapshot.output_path = output_path
        if self.on_update:
            self.on_update(self.snapshot)

    def _finish_error(self, state: str, message: str, detail: str = "") -> None:
        from ..utils.validation import safe_provider_error, safe_provider_message
        safe_message = safe_provider_message(message, "Generation failed.")
        safe_detail = safe_provider_error(detail)
        self.snapshot.error = safe_detail or safe_message
        self._update(state, self.snapshot.progress if state == STATUS_TIMEOUT else 1.0, safe_message)
        self._notify_finished(GenerationResult(job_id=self.snapshot.job_id, status=state, message=safe_message, error=self.snapshot.error))

    def _notify_finished(self, result: GenerationResult) -> None:
        if self._finished_notified:
            return
        self._finished_notified = True
        if self.on_finished:
            self.on_finished(result, self.snapshot)

    def _finish_cancelled(self) -> None:
        self._update(STATUS_CANCELLED, self.snapshot.progress, "Generation cancelled.")
        self._notify_finished(GenerationResult(job_id=self.snapshot.job_id, status=STATUS_CANCELLED, message="Generation cancelled."))
