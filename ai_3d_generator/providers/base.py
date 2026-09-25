"""Provider contract for text-to-3D generation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from ..core.capabilities import ProviderCapabilities
from ..core.errors import ValidationError
from ..core.constants import VERSION
from ..core.models import JobHandle, JobStatus, ProviderConfig
from ..utils.http import HttpClient


class Base3DProvider(ABC):
    """Minimal async-job contract implemented by every backend."""

    identifier = "base"
    display_name = "Base Provider"
    requires_api_key = False
    default_model = "default"

    def __init__(self, config: Optional[ProviderConfig] = None, client: Optional[HttpClient] = None) -> None:
        self.config = config or ProviderConfig()
        timeout = float(self.config.timeout)
        if timeout != timeout or timeout in {float("inf"), float("-inf")} or not 0.1 <= timeout <= 600.0:
            raise ValueError("timeout must be between 0.1 and 600 seconds")
        self.client = client or HttpClient(timeout=timeout, user_agent=f"AI3DGenerator/{VERSION}")
        if self.client is not None and hasattr(self.client, "set_allowed_redirect_origins"):
            try:
                self.client.set_allowed_redirect_origins((self.config.base_url,))
            except (ValidationError, OSError, RuntimeError):
                # URL validation is reported by the provider's explicit
                # validate_credentials() method, not during object construction.
                pass

    def validate_credentials(self) -> None:
        """Validate provider-specific credential requirements."""
        if self.requires_api_key and not self.config.api_key and not self.config.api_key_env:
            raise ValidationError("An API key is required for this provider.")

    @abstractmethod
    def create_generation_job(self, request: Dict[str, Any]) -> JobHandle:
        """Submit a request and return a provider job handle."""

    @abstractmethod
    def get_job_status(self, job_id: str, elapsed_seconds: float = 0.0) -> JobStatus:
        """Return a normalized provider job status."""

    def cancel_job(self, job_id: str) -> None:
        """Cancel a provider job when the provider supports it."""
        raise NotImplementedError("This provider does not expose cancellation.")

    def download_asset(self, url: str, destination: PathLikeLike) -> str:
        """Download a completed asset to a safe destination."""
        raise NotImplementedError("This provider does not provide direct download support.")

    def capabilities(self) -> ProviderCapabilities:
        """Return explicit capabilities for UI filtering and mode selection."""
        return ProviderCapabilities(
            provider_id=self.identifier,
            name=self.display_name,
            supported_generation_modes=(),
            supported_formats=(),
            supports_text_to_3d=False,
        )

    def supports_capability(self, capability: str) -> bool:
        return self.capabilities().supports(capability)

    def capabilities_for(self, config: Optional[ProviderConfig] = None) -> ProviderCapabilities:
        """Optional config-aware capability hook for configured adapters."""
        return self.capabilities()

    def models(self) -> list[str]:
        """Return locally known model identifiers without network access."""
        return [self.default_model]


# Kept as a type alias to avoid requiring Python 3.10 in the extension surface.
PathLikeLike = Any
