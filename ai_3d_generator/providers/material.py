"""Optional material-generation provider contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class MaterialProvider(ABC):
    @abstractmethod
    def generate_material(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Return provider-defined material/PBR data for Blender adaptation."""
