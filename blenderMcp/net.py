"""Minimal HTTP helpers (standard library only) for asset libraries and generators.

urllib honours HTTP(S)_PROXY / NO_PROXY from the environment, so corporate
proxies work without extra configuration.
"""

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from . import __version__

USER_AGENT = f"blender-mcp-bridge/{__version__} (+https://github.com/SHAN-code5/blender)"
DEFAULT_TIMEOUT = 30.0
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 4 * 1024 ** 3
CHUNK_BYTES = 1024 * 1024


class NetError(RuntimeError):
    """A request failed; ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, status: Optional[int] = None, retry_after: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def cache_dir(override: Optional[str] = None, platform: Optional[str] = None, environ=None,
              home: Optional[Path] = None) -> Path:
    """Where downloaded assets and generated models are kept."""
    environ = os.environ if environ is None else environ
    if override or environ.get("BLENDER_MCP_CACHE"):
        return Path(override or environ["BLENDER_MCP_CACHE"]).expanduser()
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    if platform.startswith("win"):
        return Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "blender-mcp-bridge" / "cache"
    if platform == "darwin":
        return home / "Library" / "Caches" / "blender-mcp-bridge"
    return Path(environ.get("XDG_CACHE_HOME") or home / ".cache") / "blender-mcp-bridge"


def with_params(url: str, params: Optional[Mapping[str, Any]]) -> str:
    if not params:
        return url
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return url + ("&" if "?" in url else "?") + query


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).netloc or url


def _check_scheme(url: str) -> None:
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise NetError(f"refusing to fetch a non-HTTP URL: {url!r}")


def _open(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)


def _http_error(exc: urllib.error.HTTPError, url: str) -> NetError:
    detail = ""
    try:
        body = exc.read(4096).decode("utf-8", "replace")
        data = json.loads(body)
        message = data.get("message") or data.get("detail") or data.get("error") if isinstance(data, dict) else None
        if isinstance(message, str):
            detail = f": {message[:300]}"
    except Exception:
        pass
    return NetError(f"{_host(url)} answered HTTP {exc.code}{detail}", status=exc.code,
                    retry_after=exc.headers.get("Retry-After") if exc.headers else None)


def request_json(method: str, url: str, *, params: Optional[Mapping[str, Any]] = None,
                 headers: Optional[Mapping[str, str]] = None, json_body: Any = None,
                 data: Optional[bytes] = None, content_type: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Make a request and decode the JSON answer, raising NetError on any failure."""
    url = with_params(url, params)
    _check_scheme(url)
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    elif data is not None:
        body = data
        if content_type:
            request_headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with _open(request, timeout) as response:
            raw = response.read(MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc, url) from exc
    except urllib.error.URLError as exc:
        raise NetError(f"could not reach {_host(url)}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise NetError(f"request to {_host(url)} failed: {exc}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise NetError(f"{_host(url)} sent an oversized response")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise NetError(f"{_host(url)} returned invalid JSON") from exc


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, *, headers: Optional[Mapping[str, str]] = None,
             expected_md5: Optional[str] = None, expected_size: Optional[int] = None,
             max_bytes: int = MAX_DOWNLOAD_BYTES, timeout: float = 60.0) -> Path:
    """Stream ``url`` to ``destination`` atomically; reuse a cached copy whose md5 matches."""
    _check_scheme(url)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and expected_md5 and md5_of(destination) == expected_md5:
        return destination
    request_headers = {"User-Agent": USER_AGENT}
    request_headers.update(headers or {})
    partial = destination.with_name(destination.name + ".partial")
    digest = hashlib.md5()
    received = 0
    try:
        request = urllib.request.Request(url, headers=request_headers)
        with _open(request, timeout) as response, open(partial, "wb") as handle:
            for chunk in iter(lambda: response.read(CHUNK_BYTES), b""):
                received += len(chunk)
                if received > max_bytes:
                    raise NetError(f"download from {_host(url)} exceeds {max_bytes} bytes")
                digest.update(chunk)
                handle.write(chunk)
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise _http_error(exc, url) from exc
    except urllib.error.URLError as exc:
        partial.unlink(missing_ok=True)
        raise NetError(f"could not reach {_host(url)}: {exc.reason}") from exc
    except NetError:
        partial.unlink(missing_ok=True)
        raise
    except (TimeoutError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise NetError(f"download from {_host(url)} failed: {exc}") from exc
    if expected_size is not None and received != int(expected_size):
        partial.unlink(missing_ok=True)
        raise NetError(f"download from {_host(url)} was {received} bytes, expected {expected_size}")
    if expected_md5 and digest.hexdigest() != expected_md5:
        partial.unlink(missing_ok=True)
        raise NetError(f"download from {_host(url)} failed its checksum")
    partial.replace(destination)
    return destination


_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_join(root: Path, relpath: str) -> Path:
    """Join a server-supplied relative path under root, refusing anything that escapes it."""
    if not isinstance(relpath, str) or not relpath or "\x00" in relpath:
        raise NetError(f"invalid relative path {relpath!r}")
    parts = re.split(r"[\\/]+", relpath)
    if relpath.startswith(("/", "\\")) or _DRIVE.match(relpath) or any(p in ("", ".", "..") for p in parts):
        raise NetError(f"refusing unsafe relative path {relpath!r}")
    return Path(root).joinpath(*parts)


def encode_multipart(fields: Iterable[Tuple[str, str]],
                     files: Iterable[Tuple[str, str, bytes, str]] = ()) -> Tuple[bytes, str]:
    """Encode form fields and files as multipart/form-data. Returns (body, content type)."""
    boundary = "----blender-mcp-" + uuid.uuid4().hex
    lines = []
    for name, value in fields:
        lines.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode("utf-8"))
        lines.append(str(value).encode("utf-8") + b"\r\n")
    for name, filename, payload, mime in files:
        lines.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8"))
        lines.append(payload + b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"
