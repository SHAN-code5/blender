"""Test configuration for the headless core suite."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_sessionfinish(session, exitstatus):
    session.config._final_exitstatus = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # The standalone bpy module (used by tests/blenderBridgeAddon.py) can hang in
    # Blender's native teardown after the glTF add-on has run under pytest. Results
    # are already reported here, so exit with pytest's status and skip that teardown.
    if "bpy" in sys.modules and hasattr(config, "_final_exitstatus"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(config._final_exitstatus)
