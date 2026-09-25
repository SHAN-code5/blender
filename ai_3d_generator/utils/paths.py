"""URL path construction without allowing untrusted job IDs into paths."""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

from ..core.errors import ValidationError
from .validation import validate_http_url, validate_job_id


def join_endpoint(base_url: str, endpoint: str) -> str:
    """Join a configured base URL and endpoint, preserving the base path."""
    base = validate_http_url(base_url, "API Base URL")
    if not str(endpoint or "").strip():
        raise ValidationError("API endpoint cannot be empty.")
    endpoint = str(endpoint).strip()
    joined = validate_http_url(urljoin(base.rstrip("/") + "/", endpoint), "API endpoint")
    base_url_parsed = urlparse(base)
    endpoint_url_parsed = urlparse(joined)
    base_host = (base_url_parsed.hostname or "").lower()
    endpoint_host = (endpoint_url_parsed.hostname or "").lower()
    if (
        base_url_parsed.scheme.lower() != endpoint_url_parsed.scheme.lower()
        or base_url_parsed.port != endpoint_url_parsed.port
        or (base_host and endpoint_host != base_host)
    ):
        raise ValidationError("API endpoint must stay on the configured provider origin.")
    return joined


def endpoint_for_job(template: str, base_url: str, job_id: str) -> str:
    """Render a job endpoint after validating both the template and ID."""
    safe_id = validate_job_id(job_id)
    try:
        endpoint = str(template).format(job_id=safe_id)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValidationError("The API endpoint template is invalid.") from exc
    return join_endpoint(base_url, endpoint)
