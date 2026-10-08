"""Command-line entry point: ``blender-mcp-bridge``.

    blender-mcp-bridge [serve] [--port 9876] [--transport stdio|sse|streamable-http] ...
    blender-mcp-bridge config CLIENT [--write] [--launcher uvx|python|command]
    blender-mcp-bridge install-addon [--blender-version 4.5] [--dest DIR]
    blender-mcp-bridge update [--dry-run]
    blender-mcp-bridge build-addon [--out DIR]
    blender-mcp-bridge headless [scene.blend] [--blender PATH] [--port 9876]
    blender-mcp-bridge doctor

In stdio mode nothing but MCP traffic is written to stdout; messages go to stderr.
"""

import argparse
import os
import platform
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import ConfigError, load_settings

COMMANDS = ("serve", "config", "install-addon", "update", "build-addon", "headless", "doctor")


def _add_connection_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", help="Blender bridge host (env BLENDER_MCP_HOST, default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Blender bridge port (env BLENDER_MCP_PORT, default 9876)")
    parser.add_argument("--token", help="Shared secret set in the add-on (env BLENDER_MCP_TOKEN)")
    parser.add_argument("--timeout", type=float, help="Default command timeout in seconds (env BLENDER_MCP_TIMEOUT)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blender-mcp-bridge", description="MCP server for Blender.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the MCP server (default)")
    _add_connection_flags(serve)
    serve.add_argument("--safe-mode", action="store_true",
                       help="block risky Python in execute_blender_code (env BLENDER_MCP_SAFE_MODE=1)")
    serve.add_argument("--transport", choices=("stdio", "sse", "streamable-http"),
                       help="MCP transport (env BLENDER_MCP_TRANSPORT, default stdio)")
    serve.add_argument("--http-host", help="bind address for sse/streamable-http (default 127.0.0.1)")
    serve.add_argument("--http-port", type=int, help="port for sse/streamable-http (default 8000)")

    from .clients import CLIENTS, LAUNCHERS

    config = sub.add_parser("config", help="print or write MCP client configuration")
    config.add_argument("client", choices=CLIENTS)
    config.add_argument("--launcher", choices=LAUNCHERS, default="uvx",
                        help="uvx (default, nothing to install), python (this interpreter), "
                             "or command (blender-mcp-bridge on PATH)")
    config.add_argument("--write", action="store_true", help="merge into the client's config file (keeps a .bak)")
    config.add_argument("--path", type=Path, help="config file to write instead of the default location")
    config.add_argument("--port", type=int, help="add BLENDER_MCP_PORT to the config")
    config.add_argument("--token", help="add BLENDER_MCP_TOKEN to the config")
    config.add_argument("--url", default="http://127.0.0.1:8000/mcp", help="server URL for the http client")

    install = sub.add_parser("install-addon", help="copy the Blender add-on into Blender's add-ons folder")
    install.add_argument("--blender-version", help="e.g. 4.5; default: every installed version")
    install.add_argument("--dest", type=Path, help="install into this add-ons folder instead")
    install.add_argument("--list", action="store_true", help="only show where it would be installed")

    update = sub.add_parser("update", help="update every installed copy of the Blender add-on")
    update.add_argument("--dry-run", action="store_true", help="only report what would change")

    build = sub.add_parser("build-addon", help="build installable add-on zips")
    build.add_argument("--out", type=Path, default=Path("dist"), help="output folder (default ./dist)")

    headless = sub.add_parser("headless", help="run Blender without its UI, with the bridge listening")
    headless.add_argument("file", nargs="?", help=".blend file to open (default: Blender's startup scene)")
    headless.add_argument("--blender", default=os.environ.get("BLENDER", "blender"),
                          help="Blender executable (env BLENDER, default 'blender' on PATH)")
    headless.add_argument("--host", help="address to listen on (default 127.0.0.1)")
    headless.add_argument("--port", type=int, help="port to listen on (env BLENDER_MCP_PORT, default 9876)")
    headless.add_argument("--token", help="require this shared secret (env BLENDER_MCP_TOKEN)")
    headless.add_argument("--no-code", action="store_true", help="disable execute_blender_code")
    headless.add_argument("--gpu", action="store_true", help="allow EEVEE/Workbench renders (machine has a GPU)")

    doctor = sub.add_parser("doctor", help="check the environment and the connection to Blender")
    _add_connection_flags(doctor)
    return parser


def _normalize(argv: List[str]) -> List[str]:
    """Treat a bare invocation (no subcommand) as ``serve``."""
    if argv and (argv[0] in COMMANDS or argv[0] in ("-h", "--help", "--version")):
        return argv
    return ["serve"] + argv


def _err(message: str) -> None:
    print(message, file=sys.stderr)


SDK_HINT = 'pip install "mcp>=1.10,<3"'


def cmd_serve(args) -> int:
    settings = load_settings(args)  # report configuration errors before importing the SDK
    try:
        from .server import main as serve
    except ImportError as exc:
        _err(f"The MCP SDK is not installed ({exc}). Install it with: {SDK_HINT}")
        return 1
    serve(settings)
    return 0


def cmd_config(args) -> int:
    from .clients import config_for, default_config_path, write_config

    env = {}
    if args.port:
        env["BLENDER_MCP_PORT"] = str(args.port)
    if args.token:
        env["BLENDER_MCP_TOKEN"] = args.token
    if not args.write:
        print(config_for(args.client, args.launcher, env, http_url=args.url))
        return 0
    path = args.path or default_config_path(args.client)
    if path is None:
        _err(f"{args.client} has no config file to write; paste this instead:")
        print(config_for(args.client, args.launcher, env, http_url=args.url))
        return 1
    backup = write_config(path, args.client, args.launcher, env)
    _err(f"Wrote the 'blender' server to {path}" + (f" (previous file saved as {backup})" if backup else ""))
    _err("Restart the client so it picks up the change.")
    return 0


def cmd_install_addon(args) -> int:
    from .addonTools import addon_targets, blender_config_root, install_addon

    targets = addon_targets(version=args.blender_version, dest=args.dest)
    if not targets:
        _err(f"No Blender user folder found under {blender_config_root()}. Start Blender once, "
             "or pass --blender-version X.Y or --dest PATH.")
        return 1
    for target in targets:
        if args.list:
            print(target)
            continue
        installed = install_addon(target)
        print(f"Installed to {installed}")
    if not args.list:
        print("In Blender: Edit > Preferences > Add-ons, enable 'Blender MCP Bridge', then open the "
              "3D View sidebar (N) > MCP > Start.")
    return 0


def cmd_update(args) -> int:
    from .addonTools import installed_copies, installed_version, update_copy, version_tuple

    copies = installed_copies()
    if not copies:
        print("No installed copy of the Blender add-on was found. Install it with: blender-mcp-bridge install-addon")
    for folder in copies:
        current = installed_version(folder) or "unknown"
        if current != "unknown" and version_tuple(current) >= version_tuple(__version__):
            print(f"{folder}: {current} is up to date")
            continue
        if args.dry_run:
            print(f"{folder}: would update {current} -> {__version__}")
            continue
        backup = update_copy(folder)
        print(f"{folder}: updated {current} -> {__version__} (previous file kept as {backup.name})")
    if copies and not args.dry_run:
        print("Restart Blender, or disable and re-enable 'Blender MCP Bridge', to load the new version.")
    print("The MCP server itself: uvx picks up new commits with `uvx --refresh --from "
          "git+https://github.com/SHAN-code5/blender blender-mcp-bridge`; pip installs need "
          "`pip install -U git+https://github.com/SHAN-code5/blender`. Restart your MCP client afterwards.")
    return 0


def cmd_build_addon(args) -> int:
    from .addonTools import build_zips

    zips = build_zips(args.out)
    print(f"Blender 4.2+ extension:   {zips['extension']}")
    print(f"Blender 3.0-4.1 add-on:   {zips['legacy']}")
    return 0


def cmd_headless(args) -> int:
    import subprocess

    runner = Path(__file__).resolve().parent / "runHeadless.py"
    command = [args.blender, "-b"] + ([args.file] if args.file else []) + ["--python", str(runner), "--"]
    for flag, value in (("--host", args.host), ("--port", args.port), ("--token", args.token)):
        if value:
            command += [flag, str(value)]
    if args.no_code:
        command.append("--no-code")
    env = dict(os.environ, BLENDER_MCP_GPU="1") if args.gpu else None
    try:
        return subprocess.call(command, env=env)
    except FileNotFoundError:
        _err(f"Blender executable not found: {args.blender}. Pass --blender /path/to/blender or set BLENDER.")
        return 1
    except KeyboardInterrupt:
        return 0


def cmd_doctor(args) -> int:
    from .connection import BlenderConnection, BlenderError

    settings = load_settings(args)
    healthy = True
    print(f"blender-mcp-bridge {__version__}")
    print(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}")
    try:
        from . import compat

        print(f"MCP SDK {compat.sdk_version()} (API v{compat.SDK_MAJOR})")
    except ImportError:
        print(f"MCP SDK: NOT INSTALLED - the server cannot run until you {SDK_HINT}")
        healthy = False
    print(f"Blender bridge address {settings.host}:{settings.port}, token {'set' if settings.token else 'not set'}")
    keys = {"Sketchfab": settings.sketchfab_api_key, "Poly Pizza": settings.polypizza_api_key,
            "Tripo": settings.tripo_api_key, "Hyper3D": settings.hyper3d_api_key}
    print("API keys: " + ", ".join(f"{name} {'set' if value else 'not set'}" for name, value in keys.items())
          + f"; custom generator config {'set' if settings.generation_config else 'not set'}")
    try:
        status = BlenderConnection(settings.host, settings.port, 10, settings.token).send_command(
            "get_bridge_status", timeout=10)
    except BlenderError as exc:
        print(f"Blender: NOT CONNECTED - {exc}")
        return 1
    print(f"Blender {status['blender_version']} connected (bridge {status['bridge_version']}, "
          f"{'background' if status['background'] else 'UI'} mode, "
          f"python execution {'on' if status['code_execution_allowed'] else 'off'})")
    if status.get("bridge_version") != __version__:
        print(f"Warning: add-on version {status.get('bridge_version')} differs from server {__version__}; "
              "run `blender-mcp-bridge update` to update it.")
    return 0 if healthy else 1


HANDLERS = {
    "serve": cmd_serve,
    "config": cmd_config,
    "install-addon": cmd_install_addon,
    "update": cmd_update,
    "build-addon": cmd_build_addon,
    "headless": cmd_headless,
    "doctor": cmd_doctor,
}


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(_normalize(argv))
    try:
        return HANDLERS[args.command](args)
    except ConfigError as exc:
        _err(f"Configuration error: {exc}")
        return 2


def entry_point() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry_point()
