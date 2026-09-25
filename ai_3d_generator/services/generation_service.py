"""Generation orchestration façade for text and image modes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..core.errors import ValidationError
from ..core.models import GenerationRequest, JobHandle
from ..utils.images import validate_image_path
from ..providers.image_to_3d import ImageTo3DProvider


@dataclass(frozen=True)
class GenerationMode:
    identifier: str
    label: str


class GenerationService:
    """Chooses a provider capability without embedding vendor-specific logic."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def submit_text(self, request: GenerationRequest) -> JobHandle:
        if not self._supports("text_to_3d"):
            raise ValidationError("The selected provider does not support Text → 3D.")
        return self.provider.create_generation_job(request.to_dict())

    def submit_image(self, image_path: str, request: GenerationRequest) -> JobHandle:
        if not isinstance(self.provider, ImageTo3DProvider) or not self._supports("image_to_3d"):
            raise ValidationError("The selected provider does not support Image → 3D.")
        return self.provider.create_image_job(validate_image_path(image_path), request.to_dict())

    def _supports(self, mode: str) -> bool:
        try:
            return self.provider.capabilities().supports_generation_mode(mode)
        except (AttributeError, TypeError):
            return False
