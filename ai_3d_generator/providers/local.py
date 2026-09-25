"""Local HTTP provider using the same contract as CustomRESTProvider."""
from __future__ import annotations

from .custom_rest import CustomRESTProvider


class LocalProvider(CustomRESTProvider):
    """Adapter for a local text-to-3D service.

    Configure its base URL and endpoint mappings in Preferences. It is not a
    built-in server and therefore does not claim to generate without one.
    """

    identifier = "local_api"
    display_name = "Local API"

    def capabilities(self):
        from ..core.capabilities import ProviderCapabilities
        return ProviderCapabilities(
            provider_id=self.identifier,
            name=self.display_name,
            supported_generation_modes=("text_to_3d",),
            supported_formats=("glb",),
            supports_text_to_3d=True,
            supports_cancellation=bool(str(self.config.cancel_path or "").strip()),
        )
