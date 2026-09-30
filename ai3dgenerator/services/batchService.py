"""Bounded batch queue for sequential/concurrent generation jobs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from ..core.errors import ValidationError


@dataclass
class BatchItem:
    job_id: str
    prompt: str
    state: str = "queued"
    error: str = ""
    metadata: Dict[str, object] = field(default_factory=dict)


class BatchQueue:
    """Pure-Python queue with pause/resume, bounded workers, retry, and cancel."""

    def __init__(self, max_workers: int = 1) -> None:
        if max_workers < 1 or max_workers > 8:
            raise ValidationError("Batch concurrency must be between 1 and 8.")
        self.max_workers = max_workers
        self.paused = False
        self.items: Dict[str, BatchItem] = {}
        self._pending: Deque[str] = deque()
        self._running: Dict[str, BatchItem] = {}

    def enqueue(self, job_id: str, prompt: str, metadata: Optional[Dict[str, object]] = None) -> BatchItem:
        key = str(job_id or "").strip()
        if not key or not str(prompt or "").strip():
            raise ValidationError("Batch jobs require an ID and prompt.")
        if key in self.items:
            raise ValidationError(f"Batch job already exists: {key}")
        item = BatchItem(job_id=key, prompt=str(prompt), metadata=dict(metadata or {}))
        self.items[key] = item
        self._pending.append(key)
        return item

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def claim_next(self) -> Optional[str]:
        if self.paused or len(self._running) >= self.max_workers:
            return None
        while self._pending:
            key = self._pending.popleft()
            item = self.items[key]
            if item.state == "cancelled":
                continue
            item.state = "processing"
            self._running[key] = item
            return key
        return None

    def complete(self, job_id: str, metadata: Optional[Dict[str, object]] = None) -> None:
        item = self._require(job_id)
        if item.state == "completed":
            return
        if job_id not in self._running:
            raise ValidationError("Only a processing batch job can complete.")
        self._running.pop(job_id, None)
        item.state = "completed"
        if metadata:
            item.metadata.update(metadata)

    def fail(self, job_id: str, error: str) -> None:
        item = self._require(job_id)
        if item.state == "failed":
            return
        if job_id not in self._running:
            raise ValidationError("Only a processing batch job can fail.")
        self._running.pop(job_id, None)
        item.state = "failed"
        item.error = str(error)[:1000]

    def cancel(self, job_id: str) -> None:
        if job_id not in self.items:
            return
        item = self.items[job_id]
        if item.state in {"completed", "cancelled"}:
            return
        self._running.pop(job_id, None)
        item.state = "cancelled"
        try:
            self._pending.remove(job_id)
        except ValueError:
            pass

    def pending_job_ids(self) -> List[str]:
        return list(self._pending)

    def job_ids(self) -> List[str]:
        return list(self.items)

    def remove(self, job_id: str) -> bool:
        if job_id in self._running:
            raise ValidationError("A processing batch job cannot be removed.")
        if job_id not in self.items:
            return False
        self.items.pop(job_id, None)
        try:
            self._pending.remove(job_id)
        except ValueError:
            pass
        return True

    def retry(self, job_id: str) -> BatchItem:
        item = self._require(job_id)
        if item.state == "processing":
            raise ValidationError("A processing batch job cannot be retried.")
        if item.state == "queued":
            return item
        if item.state == "completed":
            raise ValidationError("A completed batch job cannot be retried.")
        item.state = "queued"
        item.error = ""
        self._pending.append(job_id)
        return item

    def reorder(self, job_id: str, position: int = 0) -> None:
        if job_id not in self._pending:
            return
        self._pending.remove(job_id)
        self._pending.insert(max(0, min(position, len(self._pending))), job_id)

    def snapshot(self) -> List[BatchItem]:
        return list(self.items.values())

    def _require(self, job_id: str) -> BatchItem:
        try:
            return self.items[job_id]
        except KeyError as exc:
            raise ValidationError(f"Unknown batch job: {job_id}") from exc
