"""Provider capability metadata for dynamic, honest UI exposure."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Tuple


@dataclass(frozen=True)
class ProviderCapabilities:
    """Declarative capabilities advertised by one provider adapter."""

    provider_id: str
    name: str
    supported_generation_modes: Tuple[str, ...] = ()
    supported_formats: Tuple[str, ...] = ()
    supports_variation: bool = False
    supports_image_to_3d: bool = False
    supports_text_to_3d: bool = False
    supports_texture_generation: bool = False
    supports_material_generation: bool = False
    supports_prompt_enhancement: bool = False
    supports_connection_test: bool = False
    supports_cancellation: bool = False
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        modes = tuple(self.supported_generation_modes)
        formats = tuple(str(item).lower().lstrip(".") for item in self.supported_formats)
        if any(mode not in {"text_to_3d", "image_to_3d"} for mode in modes):
            raise ValueError("Provider capability contains an unsupported generation mode.")
        if any(fmt not in {"glb", "gltf", "obj", "fbx", "stl"} for fmt in formats):
            raise ValueError("Provider capability contains an unsupported output format.")
        if any(
            (mode == "text_to_3d" and not self.supports_text_to_3d)
            or (mode == "image_to_3d" and not self.supports_image_to_3d)
            for mode in modes
        ):
            raise ValueError("Provider capability flags disagree with its generation modes.")
        object.__setattr__(self, "supported_generation_modes", modes)
        object.__setattr__(self, "supported_formats", formats)

    def supported_formats_for_mode(self, mode: str = "") -> tuple[str, ...]:
        """Return only formats explicitly declared for the requested mode."""
        if mode and not self.supports_generation_mode(mode):
            return ()
        return tuple(self.supported_formats)

    def supports_generation_mode(self, mode: str) -> bool:
        """Return true only when both the mode and its feature flag agree."""
        if mode == "text_to_3d":
            return mode in self.supported_generation_modes and self.supports_text_to_3d
        if mode == "image_to_3d":
            return mode in self.supported_generation_modes and self.supports_image_to_3d
        return False

    def supports(self, capability: str) -> bool:
        """Return whether a named capability is explicitly supported."""
        if capability in {"text_to_3d", "image_to_3d"}:
            return self.supports_generation_mode(capability)
        if capability == "variation":
            return self.supports_variation
        if capability == "connection_test":
            return self.supports_connection_test
        if capability == "cancellation":
            return self.supports_cancellation
        if capability == "prompt_enhancement":
            return self.supports_prompt_enhancement
        if capability == "texture_generation":
            return self.supports_texture_generation
        if capability == "material_generation":
            return self.supports_material_generation
        return False

    def to_dict(self) -> Dict[str, Any]:
        """Return JSON-safe, secret-free capability metadata."""
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "description": self.description,
            "supported_generation_modes": list(self.supported_generation_modes),
            "supported_formats": list(self.supported_formats),
            "supports_variation": self.supports_variation,
            "supports_image_to_3d": self.supports_image_to_3d,
            "supports_text_to_3d": self.supports_text_to_3d,
            "supports_texture_generation": self.supports_texture_generation,
            "supports_material_generation": self.supports_material_generation,
            "supports_prompt_enhancement": self.supports_prompt_enhancement,
            "supports_connection_test": self.supports_connection_test,
            "supports_cancellation": self.supports_cancellation,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ProviderCapabilities":
        if not isinstance(value, dict) or not value.get("provider_id") or not value.get("name"):
            raise ValueError("Capability metadata requires provider_id and name.")

        def strict_bool(key: str) -> bool:
            raw = value.get(key, False)
            if not isinstance(raw, bool):
                raise ValueError(f"{key} must be a boolean.")
            return raw

        def strict_sequence(key: str) -> tuple[str, ...]:
            raw = value.get(key, ())
            if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) for item in raw):
                raise ValueError(f"{key} must be a sequence of strings.")
            return tuple(raw)

        known = {
            "provider_id": str(value["provider_id"]),
            "name": str(value["name"]),
            "description": str(value.get("description", "")),
            "supported_generation_modes": strict_sequence("supported_generation_modes"),
            "supported_formats": tuple(item.lower().lstrip(".") for item in strict_sequence("supported_formats")),
            "supports_variation": strict_bool("supports_variation"),
            "supports_image_to_3d": strict_bool("supports_image_to_3d"),
            "supports_text_to_3d": strict_bool("supports_text_to_3d"),
            "supports_texture_generation": strict_bool("supports_texture_generation"),
            "supports_material_generation": strict_bool("supports_material_generation"),
            "supports_prompt_enhancement": strict_bool("supports_prompt_enhancement"),
            "supports_connection_test": strict_bool("supports_connection_test"),
            "supports_cancellation": strict_bool("supports_cancellation"),
            "metadata": value.get("metadata") if isinstance(value.get("metadata"), dict) else {},
        }
        return cls(**known)


def normalize_capabilities(value: ProviderCapabilities | Dict[str, Any] | Iterable[str]) -> ProviderCapabilities:
    """Convert common provider capability inputs into one stable object."""
    if isinstance(value, ProviderCapabilities):
        return value
    if isinstance(value, dict):
        return ProviderCapabilities.from_dict(value)
    modes = tuple(value)
    return ProviderCapabilities(
        provider_id="unknown",
        name="Unknown",
        supported_generation_modes=modes,
        supports_text_to_3d="text_to_3d" in modes,
        supports_image_to_3d="image_to_3d" in modes,
    )
