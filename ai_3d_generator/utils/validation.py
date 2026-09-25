"""Validation helpers for URLs, remote JSON, identifiers, and user input."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

from ..core.errors import ProviderResponseError, ValidationError

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ALLOWED_PROTOCOLS = frozenset({"http", "https"})
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|auth[_-]?token|access[_-]?token|refresh[_-]?token|password|secret|credential)\b\s*([:=])\s*([\"']?)[^\s,;\"']+"
)
_BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")


def redact_sensitive_text(value: Any, max_length: int = 1000) -> str:
    """Return bounded provider text with common credential forms removed."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}<redacted>", text)
    text = _BEARER_RE.sub("<redacted>", text)
    return text[:max_length]


def safe_provider_error(value: Any) -> str:
    """Return a persistence-safe error label without provider-controlled text."""
    return "Provider reported an error." if str(value or "").strip() else ""


def safe_provider_message(value: Any, fallback: str = "Provider status updated.") -> str:
    """Return bounded, redacted provider status text for Scene state."""
    return redact_sensitive_text(value, max_length=1000) or fallback


def normalized_origin(value: str, label: str = "URL") -> tuple[str, str, int]:
    """Return the canonical (scheme, hostname, effective-port) origin."""
    text = validate_http_url(value, label)
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValidationError(f"{label} is missing a valid host.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationError(f"{label} contains an invalid port.") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def validate_http_url(value: str, label: str = "URL") -> str:
    """Validate an absolute HTTP(S) URL and return it unchanged."""
    text = str(value or "").strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise ValidationError(f"{label} contains control characters.")
    if "#" in text:
        raise ValidationError(f"{label} must not contain a URL fragment.")
    parsed = urlparse(text)
    if parsed.scheme.lower() not in _ALLOWED_PROTOCOLS:
        raise ValidationError(f"{label} must use http:// or https://.")
    if not parsed.netloc or parsed.hostname is None:
        raise ValidationError(f"{label} is missing a valid host.")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(f"{label} must not contain embedded credentials.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValidationError(f"{label} contains an invalid port.") from exc
    return text


def validate_job_id(value: str) -> str:
    """Reject path traversal and unsafe characters in a provider job ID."""
    text = str(value or "").strip()
    if not _JOB_ID_RE.fullmatch(text):
        raise ValidationError("Provider returned an invalid job ID.")
    return text


def validate_asset_format(value: str, supported: Iterable[str]) -> str:
    """Normalize a supported asset extension."""
    text = str(value or "").strip().lower().lstrip(".")
    allowed = {item.lower().lstrip(".") for item in supported}
    if text not in allowed:
        raise ValidationError(f"Unsupported 3D asset format: {text or 'unknown'}")
    return text


def get_by_path(value: Any, path: str, default: Any = None) -> Any:
    """Read a dotted response path, with optional numeric list indexes."""
    if path in {"", "."}:
        return value
    current = value
    for part in str(path).split("."):
        if isinstance(current, dict):
            if part not in current:
                return default
            current = current[part]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


def extract_job_id(payload: Any, path: str) -> str:
    """Extract and validate a job ID from a response payload."""
    value = get_by_path(payload, path)
    if value is None or isinstance(value, (dict, list, bool)):
        raise ProviderResponseError("Generation failed: the provider did not return a valid job ID.")
    return validate_job_id(str(value))


def normalize_status(value: Any) -> str:
    """Map common provider status spellings to the internal state set."""
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "accepted": "queued",
        "pending": "queued",
        "in_queue": "queued",
        "running": "processing",
        "in_progress": "processing",
        "generating": "processing",
        "success": "completed",
        "succeeded": "completed",
        "complete": "completed",
        "done": "completed",
        "error": "failed",
        "failure": "failed",
        "canceled": "cancelled",
        "timed_out": "timeout",
    }
    return aliases.get(text, text if text in {"queued", "processing", "completed", "failed", "cancelled", "timeout"} else "unknown")


def extract_output_url(payload: Any, path: str) -> str:
    """Extract and validate a downloadable HTTP(S) asset URL."""
    value = get_by_path(payload, path)
    if not value or isinstance(value, (dict, list, bool)):
        raise ProviderResponseError("Generation completed, but the provider did not return a download URL.")
    try:
        return validate_http_url(str(value), "Asset download URL")
    except ValidationError as exc:
        raise ProviderResponseError(exc.user_message(), detail=exc.detail) from exc


def extract_error(payload: Any, path: str) -> str:
    """Return a bounded provider error string from a response."""
    value = get_by_path(payload, path)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return "Provider returned an error without a readable message."
    return str(value)[:1000]


def parse_json_response(content: bytes, content_type: str = "") -> Dict[str, Any]:
    """Decode a JSON object response and provide a safe user-facing error."""
    import json

    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderResponseError("Provider returned invalid JSON.", detail=str(exc)) from exc
    if not isinstance(value, dict):
        raise ProviderResponseError("Provider returned JSON with an unexpected structure.")
    return value
