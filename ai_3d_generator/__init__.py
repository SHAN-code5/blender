"""AI 3D Object Generator Blender extension entry point."""
from __future__ import annotations

import importlib
import logging
from typing import Any

from .core.constants import EXTENSION_NAME, VERSION

# Import registration modules once here. All classes are registered in
# dependency order: properties/preferences, then operators, then panels.
_MODULES = (
    ".ui.properties",
    ".ui.preferences",
    ".ui.operators",
    ".ui.operators_phase3",
    ".ui.panels",
)

logger = logging.getLogger("ai_3d_generator")


def register() -> None:
    """Register the extension with Blender."""
    from .ui import runtime

    modules = [importlib.import_module(name, __package__) for name in _MODULES]
    properties = modules[0]
    preferences = modules[1]
    operators = modules[2]
    phase3 = modules[3]
    panels = modules[4]
    attempted: list[Any] = []
    for module in (properties, preferences, runtime, operators, phase3, panels):
        try:
            module.register()
        except Exception:
            for registered_module in reversed(attempted):
                try:
                    registered_module.unregister()
                except Exception:
                    logger.exception("Could not roll back %s", registered_module.__name__)
            raise
        attempted.append(module)


def unregister() -> None:
    """Unregister the extension cleanly, tolerating partial registration."""
    from .ui import runtime
    try:
        runtime.unregister()
    except (ImportError, AttributeError, RuntimeError):
        pass
    for name in reversed(_MODULES):
        try:
            module = importlib.import_module(name, __package__)
            module.unregister()
        except (ImportError, AttributeError):
            pass
        except RuntimeError:
            logger.exception("Could not unregister %s", name)


if __name__ == "__main__":
    print(f"{EXTENSION_NAME} {VERSION}")
