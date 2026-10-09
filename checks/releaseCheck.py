"""Check a Blender MCP Bridge release before it is published.

    python -m build --outdir dist && python -m blenderMcp build-addon --out dist
    python checks/releaseCheck.py --dist dist [--tag v0.4.0]

Verifies that every version string agrees (and matches the tag), that dist/ holds the
sdist, the wheel, and both add-on zips, and that the wheel installs into a fresh virtual
environment with a working command, its guides, and the add-on files.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_NAME = "blender_mcp_bridge"
ADDON_ID = "blender_mcp_bridge"


def versions():
    def find(path, pattern):
        match = re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.M)
        if not match:
            raise SystemExit(f"no version found in {path}")
        return ".".join(part.strip() for part in match.group(1).split(","))

    return {
        "pyproject.toml": find("pyproject.toml", r'^version = "([^"]+)"'),
        "blenderMcp/__init__.py": find("blenderMcp/__init__.py", r'^__version__ = "([^"]+)"'),
        "add-on BRIDGE_VERSION": find("blenderMcp/addon/__init__.py", r'^BRIDGE_VERSION = "([^"]+)"'),
        "add-on bl_info": find("blenderMcp/addon/__init__.py", r'"version": \(([\d, ]+)\)'),
        "blender_manifest.toml": find("blenderMcp/addon/blender_manifest.toml", r'^version = "([^"]+)"'),
    }


def check_versions(tag):
    found = versions()
    distinct = set(found.values())
    if len(distinct) != 1:
        raise SystemExit("versions disagree: " + ", ".join(f"{k}={v}" for k, v in found.items()))
    version = distinct.pop()
    if tag is not None and tag != "v" + version:
        raise SystemExit(f"tag {tag} does not match version {version} (expected v{version})")
    return version


def check_dist(dist, version):
    expected = {
        f"{DIST_NAME}-{version}.tar.gz", f"{DIST_NAME}-{version}-py3-none-any.whl",
        f"{ADDON_ID}-{version}.zip", f"{ADDON_ID}-{version}-legacy.zip",
    }
    present = {p.name for p in dist.iterdir() if p.is_file()}
    if present != expected:
        raise SystemExit(f"dist/ should hold exactly {sorted(expected)}, found {sorted(present)}")
    with zipfile.ZipFile(dist / f"{ADDON_ID}-{version}.zip") as archive:
        manifest = archive.read("blender_manifest.toml").decode()
        if f'version = "{version}"' not in manifest or "gifwriter.py" not in archive.namelist():
            raise SystemExit("the extension zip has the wrong version or misses files")
    with zipfile.ZipFile(dist / f"{ADDON_ID}-{version}-legacy.zip") as archive:
        if f"{ADDON_ID}/__init__.py" not in archive.namelist():
            raise SystemExit("the legacy zip misses the add-on package")
    return dist / f"{DIST_NAME}-{version}-py3-none-any.whl"


def check_wheel(wheel, version):
    with tempfile.TemporaryDirectory() as temp:
        env_dir = Path(temp) / "env"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        bin_dir = env_dir / ("Scripts" if sys.platform == "win32" else "bin")
        python = bin_dir / "python"
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", str(wheel)], check=True)
        command = str(bin_dir / "blender-mcp-bridge")
        printed = subprocess.run([command, "--version"], check=True, capture_output=True, text=True).stdout
        if version not in printed:
            raise SystemExit(f"blender-mcp-bridge --version printed {printed!r}, expected {version}")
        config = json.loads(subprocess.run([command, "config", "claude-desktop"], check=True,
                                           capture_output=True, text=True).stdout)
        if "blender" not in config.get("mcpServers", {}):
            raise SystemExit("config claude-desktop did not print a blender entry")
        probe = (  # file checks by path: importing blenderMcp.addon needs bpy
            "from pathlib import Path\n"
            "import ai3dgenerator, blenderMcp\n"
            "from blenderMcp import guides, server\n"
            "for topic in guides.TOPICS: guides.load(topic)\n"
            "assert (Path(blenderMcp.__file__).parent / 'addon' / 'gifwriter.py').is_file()\n"
            "assert (Path(ai3dgenerator.__file__).parent / 'fixtures' / 'mockAsset.glb').is_file()\n"
            "server.build_server()\n"
        )
        subprocess.run([str(python), "-c", probe], check=True, cwd=temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, help="folder with the built sdist, wheel, and add-on zips")
    parser.add_argument("--tag", help="release tag, e.g. v0.4.0")
    args = parser.parse_args()
    version = check_versions(args.tag)
    print(f"versions agree: {version}")
    if args.dist:
        wheel = check_dist(args.dist, version)
        print("dist/ holds the sdist, wheel, and both add-on zips")
        check_wheel(wheel, version)
        print("the wheel installs and runs in a fresh environment")


if __name__ == "__main__":
    main()
