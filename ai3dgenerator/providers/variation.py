"""Optional variation-generation provider contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from ..core.models import JobHandle


class VariationProvider(ABC):
    @abstractmethod
    def create_variation_job(self, source_job_id: str, request: Dict[str, Any]) -> JobHandle:
        """Submit a provider-supported variation request."""
