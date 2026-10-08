"""MCP client configuration snippets and writers."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

PACKAGE_SOURCE = "git+https://github.com/SHAN-code5/blender"
SERVER_NAME = "blender"
CLIENTS = ("claude-desktop", "claude-code", "cursor", "vscode", "windsurf", "http")
LAUNCHERS = ("uvx", "python", "command")


def launch_command(launcher: str = "uvx", python: Optional[str] = None) -> Dict[str, List[str]]:
    if launcher == "uvx":
        return {"command": "uvx", "args": ["--from", PACKAGE_SOURCE, "blender-mcp-bridge"]}
    if launcher == "python":
        return {"command": python or sys.executable, "args": ["-m", "blenderMcp"]}
    if launcher == "command":
        return {"command": "blender-mcp-bridge", "args": []}
    raise ValueError(f"launcher must be one of {', '.join(LAUNCHERS)}")


def server_entry(launcher: str = "uvx", env: Optional[Dict[str, str]] = None,
                 python: Optional[str] = None) -> dict:
    entry = dict(launch_command(launcher, python))
    if env:
        entry["env"] = dict(env)
    return entry


def config_for(client: str, launcher: str = "uvx", env: Optional[Dict[str, str]] = None,
               http_url: str = "http://127.0.0.1:8000/mcp", python: Optional[str] = None):
    """Return the config text a user pastes into the given client."""
    if client not in CLIENTS:
        raise ValueError(f"client must be one of {', '.join(CLIENTS)}")
    entry = server_entry(launcher, env, python)
    if client == "claude-code":
        parts = ["claude", "mcp", "add", SERVER_NAME]
        for key, value in (env or {}).items():
            parts += ["-e", f"{key}={value}"]
        parts += ["--", entry["command"], *entry["args"]]
        if sys.platform.startswith("win"):
            return subprocess.list2cmdline(parts)
        return " ".join(shlex.quote(p) for p in parts)
    if client == "vscode":
        return json.dumps({"servers": {SERVER_NAME: dict(entry, type="stdio")}}, indent=2)
    if client == "http":
        return json.dumps({"mcpServers": {SERVER_NAME: {"url": http_url}}}, indent=2)
    return json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2)


def default_config_path(client: str, platform: Optional[str] = None, home: Optional[Path] = None,
                        environ=None) -> Optional[Path]:
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    environ = os.environ if environ is None else environ
    if client == "claude-desktop":
        if platform.startswith("win"):
            return Path(environ.get("APPDATA") or home / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
        if platform == "darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        return Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    if client == "vscode":
        return Path.cwd() / ".vscode" / "mcp.json"
    return None


def write_config(path: Path, client: str, launcher: str = "uvx", env: Optional[Dict[str, str]] = None,
                 python: Optional[str] = None) -> Optional[Path]:
    """Merge the server entry into a JSON client config, keeping a .bak of the old file.

    Returns the backup path, or None when the file did not exist yet.
    """
    if client not in ("claude-desktop", "cursor", "windsurf", "vscode"):
        raise ValueError(f"{client} does not use a JSON config file that can be written")
    path = Path(path)
    data = {}
    backup = None
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if text.strip():
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError(f"{path} does not contain a JSON object")
        backup = path.with_name(path.name + ".bak")
        backup.write_text(text, encoding="utf-8")
    key = "servers" if client == "vscode" else "mcpServers"
    entry = server_entry(launcher, env, python)
    if client == "vscode":
        entry["type"] = "stdio"
    data.setdefault(key, {})[SERVER_NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return backup
