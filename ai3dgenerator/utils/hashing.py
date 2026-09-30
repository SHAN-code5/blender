"""Stable content hashes used for cache identity and queue deduplication."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


def content_hash(path: str | Path, algorithm: str = "sha256") -> str:
    """Hash a local file in bounded chunks; missing files are rejected."""
    if algorithm not in hashlib.algorithms_available:
        raise ValueError("Unsupported hash algorithm.")
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(str(source))
    digest = hashlib.new(algorithm)
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_hash(payload: Mapping[str, Any], algorithm: str = "sha256") -> str:
    """Hash canonical JSON-safe request data without credentials."""
    import json

    safe = {str(key): value for key, value in payload.items() if not str(key).lower().endswith(("key", "token", "secret", "password"))}
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    if algorithm not in hashlib.algorithms_available:
        raise ValueError("Unsupported hash algorithm.")
    return hashlib.new(algorithm, encoded).hexdigest()
