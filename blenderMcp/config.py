"""Runtime settings for the MCP server.

Precedence: command-line flags > environment variables > defaults.
``BLENDER_HOST`` / ``BLENDER_PORT`` are accepted as aliases so existing client
configs written for other Blender MCP servers keep working.
"""

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_TIMEOUT = 30.0
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8000

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Raised for an invalid flag or environment value."""


@dataclass(frozen=True)
class Settings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    timeout: float = DEFAULT_TIMEOUT
    token: Optional[str] = field(default=None, repr=False)
    safe_mode: bool = False
    transport: str = "stdio"
    http_host: str = DEFAULT_HTTP_HOST
    http_port: int = DEFAULT_HTTP_PORT
    cache_dir: Optional[str] = None
    sketchfab_api_key: Optional[str] = field(default=None, repr=False)
    polypizza_api_key: Optional[str] = field(default=None, repr=False)
    tripo_api_key: Optional[str] = field(default=None, repr=False)
    hyper3d_api_key: Optional[str] = field(default=None, repr=False)
    generation_config: Optional[str] = None


# Settings that are only read from the environment. The BLENDERMCP_* spellings
# are accepted so keys already exported for other Blender MCP servers work.
_ENV_ONLY = {
    "cache_dir": ("BLENDER_MCP_CACHE",),
    "sketchfab_api_key": ("BLENDER_MCP_SKETCHFAB_API_KEY", "BLENDERMCP_SKETCHFAB_API_KEY"),
    "polypizza_api_key": ("BLENDER_MCP_POLYPIZZA_API_KEY", "BLENDERMCP_POLYPIZZA_API_KEY"),
    "tripo_api_key": ("BLENDER_MCP_TRIPO_API_KEY", "TRIPO_API_KEY"),
    "hyper3d_api_key": ("BLENDER_MCP_HYPER3D_API_KEY", "BLENDERMCP_HYPER3D_API_KEY"),
    "generation_config": ("BLENDER_MCP_GENERATION_CONFIG",),
}


def parse_port(value, source: str) -> int:
    try:
        port = int(str(value).strip())
    except ValueError:
        raise ConfigError(f"{source} must be an integer port, got {value!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"{source} must be between 1 and 65535, got {port}")
    return port


def parse_timeout(value, source: str) -> float:
    try:
        timeout = float(str(value).strip())
    except ValueError:
        raise ConfigError(f"{source} must be a number of seconds, got {value!r}") from None
    if not 0 < timeout <= 3600:
        raise ConfigError(f"{source} must be between 0 and 3600 seconds, got {timeout}")
    return timeout


def parse_bool(value, source: str) -> bool:
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"{source} must be one of 1/0, true/false, yes/no, on/off; got {value!r}")


def _env(environ: Mapping[str, str], *names: str) -> Optional[tuple]:
    for name in names:
        value = environ.get(name)
        if value is not None and value.strip() != "":
            return name, value
    return None


def load_settings(flags=None, environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Build Settings from an argparse namespace (or None) and the environment."""
    environ = os.environ if environ is None else environ
    values = {}

    found = _env(environ, "BLENDER_MCP_HOST", "BLENDER_HOST")
    if found:
        values["host"] = found[1].strip()
    found = _env(environ, "BLENDER_MCP_PORT", "BLENDER_PORT")
    if found:
        values["port"] = parse_port(found[1], found[0])
    found = _env(environ, "BLENDER_MCP_TIMEOUT")
    if found:
        values["timeout"] = parse_timeout(found[1], found[0])
    found = _env(environ, "BLENDER_MCP_TOKEN")
    if found:
        values["token"] = found[1].strip()
    found = _env(environ, "BLENDER_MCP_SAFE_MODE")
    if found:
        values["safe_mode"] = parse_bool(found[1], found[0])
    found = _env(environ, "BLENDER_MCP_TRANSPORT")
    if found:
        values["transport"] = found[1].strip()
    found = _env(environ, "BLENDER_MCP_HTTP_HOST")
    if found:
        values["http_host"] = found[1].strip()
    found = _env(environ, "BLENDER_MCP_HTTP_PORT")
    if found:
        values["http_port"] = parse_port(found[1], found[0])
    for name, variables in _ENV_ONLY.items():
        found = _env(environ, *variables)
        if found:
            values[name] = found[1].strip()

    if flags is not None:
        if getattr(flags, "host", None):
            values["host"] = flags.host
        if getattr(flags, "port", None) is not None:
            values["port"] = parse_port(flags.port, "--port")
        if getattr(flags, "timeout", None) is not None:
            values["timeout"] = parse_timeout(flags.timeout, "--timeout")
        if getattr(flags, "token", None):
            values["token"] = flags.token
        if getattr(flags, "safe_mode", None):
            values["safe_mode"] = True
        if getattr(flags, "transport", None):
            values["transport"] = flags.transport
        if getattr(flags, "http_host", None):
            values["http_host"] = flags.http_host
        if getattr(flags, "http_port", None) is not None:
            values["http_port"] = parse_port(flags.http_port, "--http-port")

    transport = values.get("transport", "stdio")
    if transport not in ("stdio", "sse", "streamable-http"):
        raise ConfigError(f"transport must be stdio, sse, or streamable-http; got {transport!r}")
    return Settings(**values)
