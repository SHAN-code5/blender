"""Run the Blender MCP bridge without the Blender UI.

    blender -b [scene.blend] --python blenderMcp/runHeadless.py -- [--port 9876] [--token SECRET] [--no-code]

Useful on servers, in Docker, and in CI. Rendering defaults to Cycles on the
CPU; set BLENDER_MCP_GPU=1 if the machine has a GPU and you want EEVEE or
Workbench renders.
"""

import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load_addon():
    path = os.path.join(HERE, "addon", "__init__.py")
    spec = importlib.util.spec_from_file_location(
        "blender_mcp_bridge", path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="runHeadless.py", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("BLENDER_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BLENDER_MCP_PORT", "9876")))
    parser.add_argument("--token", default=os.environ.get("BLENDER_MCP_TOKEN", ""))
    parser.add_argument("--no-code", action="store_true", help="disable execute_blender_code")
    args = parser.parse_args(argv)
    addon = load_addon()
    addon.serve_blocking(host=args.host, port=args.port, token=args.token, allow_code=not args.no_code)


if __name__ == "__main__":
    main()
