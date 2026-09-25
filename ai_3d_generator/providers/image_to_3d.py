"""Image-to-3D provider interface; adapters stay capability-honest."""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..core.capabilities import ProviderCapabilities
from ..core.errors import ValidationError
from ..core.models import JobHandle
from ..utils.images import validate_image_path
from .base import Base3DProvider


class ImageTo3DProvider(Base3DProvider):
    """Base contract for providers that accept a local reference image."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_id=self.identifier,
            name=self.display_name,
            supported_generation_modes=(),
            supported_formats=(),
            supports_text_to_3d=False,
            supports_image_to_3d=False,
        )

    def create_image_job(self, image_path: str, request: Dict[str, Any]) -> JobHandle:
        """Optional image submission hook for capable adapters."""
        raise ValidationError(f"{self.display_name} does not support image-to-3D generation.")
