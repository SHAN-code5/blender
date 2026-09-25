"""Safe asset download manager.

Downloads are bounded, written to a deterministic cache path, and checked
before Blender sees them. Provider-specific download hooks are supported for
offline or authenticated backends, but all paths still pass through the same
format and integrity checks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from ..core.constants import MIME_FORMAT_BY_PREFIX, SUPPORTED_FORMATS
from ..core.errors import DownloadError, NetworkError, ProviderResponseError, ValidationError
from ..providers.base import Base3DProvider
from ..utils.files import atomic_write_bytes, safe_output_path
from ..utils.http import HttpClient
from ..utils.validation import validate_asset_format, validate_http_url


@dataclass
class DownloadedAsset:
    path: Path
    format: str
    content_type: str = ""


def detect_format(url: str, requested_format: str = "", content_type: str = "") -> str:
    """Choose format from URL, content type, then the explicit request setting."""
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix:
        return validate_asset_format(suffix, SUPPORTED_FORMATS)
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    mime_format = MIME_FORMAT_BY_PREFIX.get(mime, "")
    if mime_format:
        return validate_asset_format(mime_format, SUPPORTED_FORMATS)
    if requested_format:
        return validate_asset_format(requested_format, SUPPORTED_FORMATS)
    raise ProviderResponseError("The downloaded file has no recognizable 3D asset format.")


class DownloadManager:
    """Download only explicitly supported asset formats into a safe cache."""

    def __init__(
        self,
        cache_dir: Path,
        timeout: float = 60.0,
        max_size_mb: int = 512,
        client: Optional[HttpClient] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.max_size_mb = max(1, int(max_size_mb))
        allowed_hosts = {str(host).strip().lower().rstrip(".") for host in (allowed_hosts or ()) if str(host).strip()}
        self.allowed_hosts = allowed_hosts
        if client is None:
            self.client = HttpClient(timeout=timeout, allowed_redirect_hosts=self.allowed_hosts)
        else:
            self.client = client
            setter = getattr(self.client, "set_allowed_redirect_origins", None)
            if callable(setter):
                setter(self._redirect_origins_for())

    def _validate_download_url(self, url: str, provider: Any = None) -> str:
        target = validate_http_url(url, "Asset download URL")
        host = (urlparse(target).hostname or "").lower().rstrip(".")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise DownloadError("The asset host is not in the configured allowlist.")
        if getattr(provider, "uses_authenticated_download", False):
            from ..utils.validation import normalized_origin

            if normalized_origin(target, "Asset download URL") != normalized_origin(str(provider.config.base_url), "API Base URL"):
                raise DownloadError("Authenticated asset downloads must use the configured provider origin.")
        return target

    def _redirect_origins_for(self, provider: Any = None, initial_url: str = "") -> set[tuple[str, str, int]]:
        """Return exact origins every redirect may visit for this download."""
        from ..utils.validation import normalized_origin

        origins: set[tuple[str, str, int]] = set()
        if initial_url:
            origins.add(normalized_origin(initial_url, "Asset download URL"))
        if getattr(provider, "uses_authenticated_download", False):
            origins.add(normalized_origin(str(provider.config.base_url), "API Base URL"))
        return origins

    def _configure_redirect_policy(self, client: Any, provider: Any = None, initial_url: str = "") -> None:
        origins = self._redirect_origins_for(provider, initial_url)
        if not origins:
            return
        setter = getattr(client, "set_allowed_redirect_origins", None)
        if not callable(setter):
            raise DownloadError("The download client cannot enforce the configured redirect allowlist.")
        setter(origins)

    def download(self, url: str, job_id: str, requested_format: str = "", provider: Any = None) -> DownloadedAsset:
        """Download and validate an asset, returning its final local path."""
        target_url = self._validate_download_url(url, provider)
        detected = detect_format(target_url, requested_format, "")
        destination = safe_output_path(self.cache_dir, job_id, "asset", detected)
        temporary = destination.with_name(".partial-" + destination.name)
        try:
            provider_hook = provider is not None and type(provider).download_asset is not Base3DProvider.download_asset
            if provider_hook:
                self._configure_redirect_policy(getattr(provider, "client", None), provider, target_url)
                provider.download_asset(target_url, temporary)
            else:
                self._configure_redirect_policy(self.client, provider, target_url)
                try:
                    response = self.client.request(
                        "GET",
                        target_url,
                        timeout=self.client.timeout,
                        max_bytes=self.max_size_mb * 1024 * 1024,
                    )
                except NetworkError as exc:
                    detail = exc.technical_message()
                    message = (
                        "Asset download was blocked by the configured allowlist."
                        if "allowlist" in detail.lower()
                        else "Asset download could not reach the provider."
                    )
                    raise DownloadError(message, detail=detail) from exc
                content_type = str(response.headers.get("Content-Type", ""))
                final_format = detect_format(target_url, requested_format, content_type)
                if final_format != detected:
                    detected = final_format
                    destination = safe_output_path(self.cache_dir, job_id, "asset", detected)
                    temporary = destination.with_name(".partial-" + destination.name)
                atomic_write_bytes(destination, response.body)
            if not temporary.exists() and not destination.exists():
                raise DownloadError("The provider returned no downloaded file.")
            source = temporary if temporary.exists() else destination
            if source != destination:
                source.replace(destination)
            validate_asset_file(destination, detected, self.max_size_mb)
            return DownloadedAsset(path=destination, format=detected)
        except (DownloadError, ValidationError, ProviderResponseError):
            raise
        except Exception as exc:
            raise DownloadError("Asset download failed or the response was corrupted.", detail=str(exc)) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def validate_asset_file(path: Path, asset_format: str, max_size_mb: int = 512) -> None:
    """Perform cheap format-specific integrity checks before Blender import."""
    if not path.is_file() or path.stat().st_size == 0:
        raise DownloadError("The downloaded 3D file is empty.")
    size = path.stat().st_size
    if size > max_size_mb * 1024 * 1024:
        raise DownloadError("The downloaded 3D file exceeds the configured size limit.")
    with path.open("rb") as handle:
        header = handle.read(64)
    fmt = validate_asset_format(asset_format, SUPPORTED_FORMATS)
    if fmt == "glb":
        if len(header) < 12 or header[:4] != b"glTF" or int.from_bytes(header[4:8], "little") != 2:
            raise DownloadError("The downloaded GLB file is corrupted.")
        declared_length = int.from_bytes(header[8:12], "little")
        if declared_length != size:
            raise DownloadError("The GLB file length does not match its header.")
    elif fmt == "gltf":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DownloadError("The downloaded glTF JSON is corrupted.") from exc
        if not isinstance(value, dict) or "asset" not in value:
            raise DownloadError("The downloaded glTF file has no asset metadata.")
    elif fmt == "obj":
        if b"\x00" in header:
            raise DownloadError("The downloaded OBJ file contains binary data.")
    elif fmt == "stl":
        if header[:5] != b"solid" and size < 84:
            raise DownloadError("The downloaded STL file is not a valid ASCII or binary STL.")
    elif fmt == "fbx":
        if b"FBX" not in header and b"Kaydara" not in header:
            raise DownloadError("The downloaded FBX file has no recognizable FBX header.")
