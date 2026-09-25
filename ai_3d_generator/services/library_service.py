"""Replaceable lightweight local asset-library storage."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..core.constants import SUPPORTED_FORMATS
from ..core.errors import ValidationError
from ..utils.files import atomic_write_bytes, ensure_directory

SCHEMA_VERSION = 1
MAX_ASSETS = 5000
_ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_KEYS = frozenset({"api_key", "authorization", "token", "secret", "password", "credential"})


def _validate_asset_id(value: Any) -> str:
    asset_id = str(value or "").strip()
    if not _ASSET_ID_RE.fullmatch(asset_id):
        raise ValidationError("Asset IDs may contain only letters, numbers, dots, underscores, and hyphens.")
    return asset_id


def _has_secret_key(value: Dict[str, Any]) -> bool:
    return any(str(key).lower() in _SECRET_KEYS for key in value)


@dataclass
class AssetMetadata:
    id: str
    prompt: str
    provider: str
    model: str
    created_at: str
    format: str
    file: str
    name: str = ""
    thumbnail: str = ""
    tags: List[str] = field(default_factory=list)
    favorite: bool = False
    collection: str = ""
    status: str = "completed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "AssetMetadata":
        if not isinstance(value, dict):
            raise ValueError("Asset metadata must be an object.")
        required = ("id", "prompt", "provider", "model", "created_at", "format", "file")
        if any(not isinstance(value.get(key), str) or not value[key] for key in required):
            raise ValueError("Asset metadata is missing a required field.")
        if _has_secret_key(value):
            raise ValueError("Asset metadata contains a credential-like field.")
        fmt = str(value["format"]).lower().lstrip(".")
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError("Asset metadata has an unsupported format.")
        tags = value.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("Asset tags must be strings.")
        return cls(
            id=value["id"],
            prompt=value["prompt"],
            name=str(value.get("name") or value["prompt"])[:1000],
            provider=value["provider"],
            model=value["model"],
            created_at=value["created_at"],
            format=fmt,
            file=value["file"],
            thumbnail=str(value.get("thumbnail", "")),
            tags=[tag.strip().lower()[:64] for tag in tags if tag.strip()][:32],
            favorite=bool(value.get("favorite", False)),
            collection=str(value.get("collection", ""))[:128],
            status=str(value.get("status", "completed"))[:32],
        )


class AssetLibrary:
    """JSON-backed library with bounded metadata and no credentials."""

    def __init__(self, root: str | Path) -> None:
        self.root = ensure_directory(root).resolve()
        self.metadata_path = self.root / "metadata.json"
        self.assets_dir = (self.root / "generated").resolve()
        self.previews_dir = (self.root / "previews").resolve()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.previews_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_contained_file(self, value: str) -> Path:
        path = Path(value).expanduser()
        try:
            path.relative_to(self.assets_dir)
        except ValueError as exc:
            raise ValidationError("Library asset path is outside the generated asset directory.") from exc
        resolved = path.resolve()
        try:
            resolved.relative_to(self.assets_dir)
        except ValueError as exc:
            raise ValidationError("Library asset path escapes the generated asset directory.") from exc
        if path.is_symlink() or resolved.is_symlink():
            raise ValidationError("Library asset path must be a regular contained file.")
        if not resolved.is_file():
            raise ValidationError("Library asset path must be a regular contained file.")
        return resolved

    def _resolve_contained_thumbnail(self, value: str) -> str:
        if not str(value or "").strip():
            return ""
        path = Path(value).expanduser()
        try:
            path.relative_to(self.previews_dir)
        except ValueError as exc:
            raise ValidationError("Library thumbnails must stay in the preview directory.") from exc
        resolved = path.resolve()
        try:
            resolved.relative_to(self.previews_dir)
        except ValueError as exc:
            raise ValidationError("Library thumbnail path escapes the preview directory.") from exc
        if path.is_symlink() or resolved.is_symlink():
            raise ValidationError("Library thumbnail path must be a regular contained file.")
        if not resolved.is_file():
            return ""
        return str(resolved)

    def _thumbnail_exists(self, value: str) -> bool:
        if not str(value or "").strip():
            return True
        try:
            return bool(self._resolve_contained_thumbnail(value))
        except ValidationError:
            return False

    def resolve_asset_path(self, value: str) -> Path:
        """Return a validated contained path for importer callers."""
        return self._resolve_contained_file(value)

    def list_assets(self) -> List[AssetMetadata]:
        if not self.metadata_path.exists():
            return []
        try:
            value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION or not isinstance(value.get("assets"), list):
            return []
        result: List[AssetMetadata] = []
        for row in value["assets"][-MAX_ASSETS:]:
            try:
                entry = AssetMetadata.from_dict(row)
                self._resolve_contained_file(entry.file)
                if entry.thumbnail and not self._thumbnail_exists(entry.thumbnail):
                    continue
                result.append(entry)
            except (TypeError, ValueError, ValidationError):
                continue
        return result

    def add_asset(
        self,
        *,
        asset_id: str,
        prompt: str,
        provider: str,
        model: str,
        output_format: str,
        file_path: str | Path,
        name: str = "",
        thumbnail_path: str = "",
        tags: Iterable[str] = (),
        favorite: bool = False,
        collection: str = "",
        copy_file: bool = False,
    ) -> AssetMetadata:
        asset_id = _validate_asset_id(asset_id)
        if not str(prompt).strip() and not str(name).strip() or not str(provider).strip() or not str(model).strip():
            raise ValidationError("Asset metadata is incomplete.")
        stored_prompt = str(prompt).strip() or "Image reference"
        if not str(name).strip():
            name = stored_prompt
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise ValidationError("Asset file does not exist.")
        if source.suffix.lower() not in {".glb", ".gltf", ".obj", ".fbx", ".stl"}:
            raise ValidationError("Asset file has an unsupported 3D format.")
        if copy_file:
            destination = (self.assets_dir / asset_id).with_suffix(Path(source).suffix.lower())
            if destination.is_symlink():
                destination.unlink()
            elif destination.exists():
                rows = self.list_assets()
                for row in rows:
                    if row.id == asset_id and Path(row.file).resolve() == destination.resolve():
                        return row
                raise ValidationError("Library destination already exists; use a new asset ID.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            atomic_write_bytes(destination, data)
            source = destination
            try:
                self._resolve_contained_file(str(source))
            except ValidationError:
                destination.unlink(missing_ok=True)
                raise
        try:
            source.relative_to(self.root)
        except ValueError as exc:
            raise ValidationError("Library assets must be contained by the library root.") from exc
        try:
            self._resolve_contained_file(str(source))
        except ValidationError as exc:
            raise ValidationError("Library assets must be stored in the generated asset directory.") from exc
        fmt = str(output_format).lower().lstrip(".")
        if fmt not in SUPPORTED_FORMATS:
            raise ValidationError("Asset format is not supported.")
        tag_values = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
        if len(tag_values) > 32 or any(len(tag) > 64 for tag in tag_values):
            raise ValidationError("Asset metadata accepts at most 32 tags of 64 characters each.")
        thumbnail = self._resolve_contained_thumbnail(thumbnail_path)
        entry = AssetMetadata(
            id=asset_id,
            prompt=stored_prompt,
            name=str(name or stored_prompt).strip()[:1000] or stored_prompt[:1000],
            provider=str(provider).strip(),
            model=str(model).strip(),
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            format=fmt,
            file=str(source),
            thumbnail=thumbnail,
            tags=[str(tag).strip().lower()[:64] for tag in tags if str(tag).strip()][:32],
            favorite=bool(favorite),
            collection=str(collection).strip()[:128],
        )
        rows = [item for item in self.list_assets() if item.id != entry.id]
        rows.append(entry)
        self._write(rows)
        return entry

    def update(self, asset_id: str, **changes: Any) -> Optional[AssetMetadata]:
        asset_id = _validate_asset_id(asset_id)
        rows = self.list_assets()
        found = None
        for entry in rows:
            if entry.id == asset_id:
                for key, value in changes.items():
                    if key == "favorite":
                        entry.favorite = bool(value)
                    elif key in {"prompt", "name", "collection", "status"}:
                        setattr(entry, key, str(value)[:1000])
                    elif key == "thumbnail":
                        entry.thumbnail = self._resolve_contained_thumbnail(str(value))
                    elif key == "tags":
                        entry.tags = [str(tag).strip().lower()[:64] for tag in value if str(tag).strip()][:32]
                    else:
                        raise ValidationError(f"Unsupported asset metadata field: {key}")
                found = entry
                break
        if found is not None:
            self._write(rows)
        return found

    def remove(self, asset_id: str) -> bool:
        asset_id = _validate_asset_id(asset_id)
        rows = self.list_assets()
        remaining = [entry for entry in rows if entry.id != asset_id]
        if len(remaining) == len(rows):
            return False
        self._write(remaining)
        return True

    def _write(self, rows: List[AssetMetadata]) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "assets": [row.to_dict() for row in rows[-MAX_ASSETS:]]}
        atomic_write_bytes(self.metadata_path, json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))

    def count(self) -> int:
        """Return the number of valid metadata rows currently persisted."""
        return len(self.list_assets())
