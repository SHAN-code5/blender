"""Optional provider-dispatched prompt enhancement contract.

This module deliberately does not contain a fake language model. A real
provider can implement the interface; the local template enhancer remains the
fallback when no prompt-enhancement capability is advertised.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class PromptEnhancementProvider(ABC):
    @abstractmethod
    def enhance_prompt(self, prompt: str, context: Dict[str, Any] | None = None) -> str:
        """Return an enhanced prompt without generating a 3D model."""
