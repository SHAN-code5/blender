"""JSON implementation of the replaceable storage boundary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from ..utils.files import atomic_write_bytes, ensure_directory
from .base import StorageProvider

MAX_RECORDS = 5000


class JSONStorage(StorageProvider):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        ensure_directory(self.path.parent)

    def read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def write(self, document: Dict[str, Any]) -> None:
        if not isinstance(document, dict):
            raise TypeError("Storage document must be an object.")
        atomic_write_bytes(self.path, json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8"))

    def list_records(self, key: str) -> List[Dict[str, Any]]:
        value = self.read().get(key, [])
        if not isinstance(value, list):
            return []
        return [item for item in value[-MAX_RECORDS:] if isinstance(item, dict)]
