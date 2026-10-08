"""Get files from the MCP server's disk to Blender's.

When Blender runs on the same machine, the bridge can read the server's paths
directly. When it does not (another computer, a container), the files are sent
over the bridge connection in chunks and written to Blender's temp folder.
"""

import base64
import uuid
from pathlib import Path
from typing import Callable, Dict, List

CHUNK_BYTES = 4 * 1024 * 1024  # base64 grows this to ~5.6 MB, under the bridge's 16 MB limit


def stage_files(call: Callable, root: Path, files: List[Path]) -> Dict[Path, str]:
    """Return a mapping from each local file to the path Blender should open.

    ``call(command, params)`` sends one bridge command. ``files[0]`` is used to
    detect a shared filesystem; all files must live under ``root``.
    """
    root = Path(root)
    files = [Path(f) for f in files]
    if not files:
        return {}
    probe = files[0]
    status = call("stat_file", {"filepath": str(probe)})
    if status.get("exists") and status.get("size") == probe.stat().st_size:
        return {f: str(f) for f in files}

    transfer_id = uuid.uuid4().hex
    staged: Dict[Path, str] = {}
    for path in files:
        relpath = path.relative_to(root).as_posix()
        offset = 0
        result = None
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(CHUNK_BYTES)
                if not chunk and offset:
                    break
                result = call("receive_file", {
                    "transfer_id": transfer_id,
                    "relpath": relpath,
                    "offset": offset,
                    "data_base64": base64.b64encode(chunk).decode("ascii"),
                })
                offset += len(chunk)
                if not chunk:
                    break  # empty file: one call creates it
        staged[path] = result["filepath"]
    return staged
