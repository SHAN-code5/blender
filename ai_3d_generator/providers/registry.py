"""Registry of provider adapters, with lazy imports and duplicate protection."""
from __future__ import annotations

from typing import Dict, Type

from .base import Base3DProvider
from .custom_rest import CustomRESTProvider
from .local import LocalProvider
from .mock import MockProvider

PROVIDERS: Dict[str, Type[Base3DProvider]] = {
    CustomRESTProvider.identifier: CustomRESTProvider,
    LocalProvider.identifier: LocalProvider,
    MockProvider.identifier: MockProvider,
}


def register_provider(provider_class: Type[Base3DProvider]) -> Type[Base3DProvider]:
    """Register a provider class for UI and service discovery."""
    identifier = str(provider_class.identifier or "").strip()
    if not identifier:
        raise ValueError("Provider identifier cannot be empty.")
    existing = PROVIDERS.get(identifier)
    if existing is not None and existing is not provider_class:
        raise ValueError(f"Provider already registered: {identifier}")
    PROVIDERS[identifier] = provider_class
    return provider_class


def get_provider_class(identifier: str) -> Type[Base3DProvider]:
    """Return a registered provider class or raise a useful error."""
    key = str(identifier or "").strip()
    try:
        return PROVIDERS[key]
    except KeyError as exc:
        available = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"Unknown provider '{key}'. Available: {available}") from exc


def provider_choices() -> list[tuple[str, str, str]]:
    """Return Blender-friendly dynamic enum tuples."""
    return [(cls.identifier, cls.display_name, cls.__doc__ or "3D generation provider") for cls in PROVIDERS.values()]


def available_providers() -> list[str]:
    return list(PROVIDERS)
