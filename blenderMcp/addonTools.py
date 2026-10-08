"""Build and install the Blender add-on without needing Blender itself."""

import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__

ADDON_ID = "blender_mcp_bridge"
ADDON_SOURCE = Path(__file__).resolve().parent / "addon"
_VERSION_DIR = re.compile(r"^\d+\.\d+$")


def addon_files() -> List[Path]:
    return sorted(p for p in ADDON_SOURCE.iterdir()
                  if p.is_file() and p.suffix in (".py", ".toml") and not p.name.startswith("."))


def build_zips(out_dir: Path) -> Dict[str, Path]:
    """Write the extension zip (Blender 4.2+) and the legacy add-on zip (Blender 3.0 - 4.1)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    extension = out_dir / f"{ADDON_ID}-{__version__}.zip"
    legacy = out_dir / f"{ADDON_ID}-{__version__}-legacy.zip"
    with zipfile.ZipFile(extension, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in addon_files():
            archive.write(path, path.name)
    with zipfile.ZipFile(legacy, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in addon_files():
            if path.suffix == ".py":
                archive.write(path, f"{ADDON_ID}/{path.name}")
    return {"extension": extension, "legacy": legacy}


def blender_config_root(platform: Optional[str] = None, environ=None, home: Optional[Path] = None) -> Path:
    """Directory holding Blender's per-version user folders (4.5/, 5.0/, ...)."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    home = Path(home) if home else Path.home()
    if platform.startswith("win"):
        appdata = environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return Path(appdata) / "Blender Foundation" / "Blender"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Blender"
    config = environ.get("XDG_CONFIG_HOME") or str(home / ".config")
    return Path(config) / "blender"


def _version_key(name: str):
    major, minor = name.split(".")
    return int(major), int(minor)


def installed_versions(root: Path) -> List[str]:
    if not root.is_dir():
        return []
    names = [p.name for p in root.iterdir() if p.is_dir() and _VERSION_DIR.match(p.name)]
    return sorted(names, key=_version_key)


def addon_targets(version: Optional[str] = None, dest: Optional[Path] = None, platform: Optional[str] = None,
                  environ=None, home: Optional[Path] = None) -> List[Path]:
    """Add-on folders to install into, in order of preference."""
    environ = os.environ if environ is None else environ
    if dest:
        return [Path(dest)]
    if environ.get("BLENDER_USER_SCRIPTS"):
        return [Path(environ["BLENDER_USER_SCRIPTS"]) / "addons"]
    root = blender_config_root(platform, environ, home)
    versions = [version] if version else installed_versions(root)
    return [root / v / "scripts" / "addons" for v in versions]


def install_addon(target_dir: Path) -> Path:
    """Copy the add-on into ``target_dir/blender_mcp_bridge`` as a legacy add-on folder.

    Legacy add-on folders load on every Blender version from 3.0, including 4.2+,
    where they appear under Add-ons alongside extensions.
    """
    destination = Path(target_dir) / ADDON_ID
    destination.mkdir(parents=True, exist_ok=True)
    for path in addon_files():
        if path.suffix == ".py":
            shutil.copy2(path, destination / path.name)
    return destination


_BRIDGE_VERSION = re.compile(r'^BRIDGE_VERSION = "([^"]+)"', re.M)


def version_tuple(version: str):
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def installed_copies(platform: Optional[str] = None, environ=None, home: Optional[Path] = None) -> List[Path]:
    """Every folder holding an installed copy of the add-on (legacy add-on or 4.2+ extension)."""
    environ = os.environ if environ is None else environ
    candidates = []
    if environ.get("BLENDER_USER_SCRIPTS"):
        candidates.append(Path(environ["BLENDER_USER_SCRIPTS"]) / "addons" / ADDON_ID)
    if environ.get("BLENDER_USER_EXTENSIONS"):
        candidates.append(Path(environ["BLENDER_USER_EXTENSIONS"]) / "user_default" / ADDON_ID)
    root = blender_config_root(platform, environ, home)
    for version in installed_versions(root):
        candidates.append(root / version / "scripts" / "addons" / ADDON_ID)
        candidates.append(root / version / "extensions" / "user_default" / ADDON_ID)
    return [c for c in candidates if (c / "__init__.py").is_file()]


def installed_version(folder: Path) -> Optional[str]:
    try:
        match = _BRIDGE_VERSION.search((Path(folder) / "__init__.py").read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def update_copy(folder: Path) -> Path:
    """Overwrite an installed copy with this version, keeping __init__.py.bak. Returns the backup."""
    folder = Path(folder)
    backup = folder / "__init__.py.bak"
    shutil.copy2(folder / "__init__.py", backup)
    is_extension = (folder / "blender_manifest.toml").exists()
    for path in addon_files():
        if path.suffix == ".py" or is_extension:
            shutil.copy2(path, folder / path.name)
    return backup
