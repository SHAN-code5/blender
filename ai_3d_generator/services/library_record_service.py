"""Generated-asset library recording and test hooks."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .library_service import AssetLibrary, AssetMetadata
from ..utils.hashing import content_hash


def record_generated_asset(
    library: AssetLibrary,
    *,
    file_path: str | Path,
    job_id: str,
    prompt: str,
    provider: str,
    model: str,
    output_format: str,
    name: str = "",
    tags: tuple[str, ...] = (),
    copy_file: bool = True,
) -> AssetMetadata:
    """Add a completed local asset using content identity and bounded metadata."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Cannot record a missing generated asset.")
    asset_id = f"{provider}-{job_id}-{content_hash(path)[:12]}"
    safe_prompt = str(prompt).strip() or "Image reference"
    return library.add_asset(
        asset_id=asset_id,
        name=(name or safe_prompt)[:1000],
        prompt=safe_prompt[:4000],
        provider=provider,
        model=model,
        output_format=output_format,
        file_path=path,
        tags=tags,
        copy_file=copy_file,
    )
