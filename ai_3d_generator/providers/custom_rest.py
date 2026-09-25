"""Configurable generic REST provider.

The adapter is intentionally honest: users map their provider's actual JSON
shape through preferences. No provider is claimed to work without configuring
its endpoints and response paths.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ..core.capabilities import ProviderCapabilities
from ..core.errors import AuthenticationError, ConfigurationError, ProviderResponseError, ValidationError
from ..core.models import JobHandle, JobStatus, ProviderConfig
from ..utils.paths import endpoint_for_job, join_endpoint
from ..utils.validation import (
    extract_error,
    extract_job_id,
    extract_output_url,
    get_by_path,
    normalize_status,
    safe_provider_error,
    safe_provider_message,
    validate_http_url,
)
from .base import Base3DProvider


class CustomRESTProvider(Base3DProvider):
    """REST adapter for APIs following a configurable async job protocol."""

    identifier = "custom_api"
    display_name = "Custom REST API"
    requires_api_key = False
    uses_authenticated_download = True

    def _resolved_api_key(self) -> str:
        if self.config.api_key:
            return self.config.api_key
        if self.config.api_key_env:
            return os.environ.get(self.config.api_key_env, "").strip()
        return ""

    def _headers(self) -> Dict[str, str]:
        headers = {str(key): str(value) for key, value in self.config.headers.items() if str(key).strip()}
        key = self._resolved_api_key()
        if key:
            if not self.config.auth_header.strip():
                raise ConfigurationError("Authentication header cannot be empty.")
            headers[self.config.auth_header] = f"{self.config.auth_prefix}{key}"
        return headers

    def validate_credentials(self) -> None:
        super().validate_credentials()
        if not self.config.base_url:
            raise ConfigurationError("API Base URL is required for the custom provider.")
        validate_http_url(self.config.base_url, "API Base URL")
        for label, endpoint in (("Generate", self.config.generate_path), ("Status", self.config.status_path), ("Cancel", self.config.cancel_path)):
            if label == "Cancel" and not str(endpoint or "").strip():
                continue
            if any(char in str(endpoint) for char in "\r\n"):
                raise ConfigurationError(f"{label} endpoint contains invalid characters.")
            try:
                join_endpoint(self.config.base_url, endpoint)
            except Exception as exc:
                raise ConfigurationError(f"{label} endpoint must stay on the configured provider origin.") from exc
        if self.config.requires_api_key:
            self._require_key()
        if self.config.requires_api_key or self._resolved_api_key():
            auth_header = str(self.config.auth_header or "").strip()
            if not auth_header:
                raise ConfigurationError("Authentication header cannot be empty when an API key is configured.")
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in auth_header):
                raise ConfigurationError("Authentication header contains invalid characters.")
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in str(self.config.auth_prefix or "")):
                raise ConfigurationError("Authentication prefix contains invalid characters.")
        if not str(self.config.cancel_path or "").strip():
            self.config.cancel_path = ""

    def _require_key(self) -> None:
        if not self._resolved_api_key():
            raise AuthenticationError("Enter an API key or set the configured environment variable.")

    def create_generation_job(self, request: Dict[str, Any]) -> JobHandle:
        payload = dict(self.config.request_payload)
        payload.update(request)
        # Local image paths are UI/runtime data, never remote API fields. A real
        # image adapter must upload bytes through its own explicit contract.
        payload.pop("image_path", None)
        payload.pop("reference_image_path", None)
        payload["model"] = self.config.model
        url = join_endpoint(self.config.base_url, self.config.generate_path)
        data, _ = self.client.request_json("POST", url, headers=self._headers(), json_body=payload, timeout=self.config.timeout)
        job_id = extract_job_id(data, self.config.job_id_path)
        return JobHandle(job_id=job_id, provider=self.identifier, raw=data)

    def get_job_status(self, job_id: str, elapsed_seconds: float = 0.0) -> JobStatus:
        url = endpoint_for_job(self.config.status_path, self.config.base_url, job_id)
        data, _ = self.client.request_json("GET", url, headers=self._headers(), timeout=self.config.timeout)
        state = normalize_status(get_by_path(data, self.config.status_path_value))
        if state == "unknown":
            raise ProviderResponseError("Provider returned an unknown job status.")
        progress = _as_progress(get_by_path(data, self.config.progress_path))
        message = safe_provider_message(get_by_path(data, "message", ""), "")
        error = safe_provider_error(extract_error(data, self.config.error_path))
        output_url = None
        if state == "completed":
            try:
                output_url = extract_output_url(data, self.config.output_url_path)
            except ValidationError as exc:
                raise ProviderResponseError(exc.user_message(), detail=exc.detail) from exc
        return JobStatus(state=state, progress=progress, message=message, output_url=output_url, error=error, raw=data)

    def cancel_job(self, job_id: str) -> None:
        if not str(self.config.cancel_path or "").strip():
            raise NotImplementedError("This provider does not expose cancellation.")
        url = endpoint_for_job(self.config.cancel_path, self.config.base_url, job_id)
        self.client.request("POST", url, headers=self._headers(), timeout=self.config.timeout)

    def download_asset(self, url: str, destination: Any) -> str:
        from ..utils.validation import normalized_origin, validate_http_url
        target = validate_http_url(url, "Asset download URL")
        if normalized_origin(target, "Asset download URL") != normalized_origin(self.config.base_url, "API Base URL"):
            raise ValidationError("Authenticated asset downloads must use the configured provider origin.")
        response = self.client.request("GET", target, headers=self._headers(), timeout=self.config.timeout, max_bytes=max(1, int(self.config.max_asset_size_mb)) * 1024 * 1024)
        destination.parent.mkdir(parents=True, exist_ok=True)
        from ..utils.files import atomic_write_bytes
        atomic_write_bytes(destination, response.body)
        return str(destination)

    def models(self) -> list[str]:
        return [self.config.model or self.default_model]

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.identifier,
            name=self.display_name,
            supported_generation_modes=("text_to_3d",),
            supported_formats=("glb",),
            supports_text_to_3d=True,
            supports_cancellation=bool(str(self.config.cancel_path or "").strip()),
            description="Configured text-to-3D adapter; only GLB output and configured cancellation are declared.",
        )


def _as_progress(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))
