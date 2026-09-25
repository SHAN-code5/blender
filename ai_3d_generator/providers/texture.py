"""Optional texture-generation provider contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class TextureProvider(ABC):
    @abstractmethod
    def generate_texture(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Return provider-defined PBR texture data or references."""
