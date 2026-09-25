"""Replaceable storage interfaces for Phase 3 metadata."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterable, List, Optional


class StorageProvider(ABC):
    """Small persistence boundary; no Blender or database dependency."""

    @abstractmethod
    def read(self) -> Dict[str, Any]:
        """Return a JSON-compatible document."""

    @abstractmethod
    def write(self, document: Dict[str, Any]) -> None:
        """Persist a JSON-compatible document atomically."""

    @abstractmethod
    def list_records(self, key: str) -> List[Dict[str, Any]]:
        """Return a bounded list of record dictionaries."""
