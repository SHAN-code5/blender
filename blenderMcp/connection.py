"""TCP client that forwards MCP tool calls to the Blender add-on.

Protocol: one JSON object per line, one request per connection.

    request  -> {"id": "...", "type": "<command>", "params": {...},
                 "timeout": <seconds>, "token": "<optional shared secret>"}
    response -> {"id": "...", "status": "success", "result": ...}
                {"id": "...", "status": "error", "message": "...", "code": "..."}

Opening a fresh localhost connection per request keeps the client stateless:
concurrent tool calls never share a socket, and a restarted Blender is picked
up on the next call without any reconnect logic.
"""

import json
import socket
import uuid
from typing import Any, Optional

from .config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT

CONNECT_TIMEOUT = 5.0
# Extra time allowed for Blender to answer after its own command timeout.
RESPONSE_GRACE = 10.0
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

NOT_RUNNING_HINT = (
    "Start the bridge in Blender (3D View > Sidebar (N) > MCP > Start), "
    "or run Blender headless with: blender -b --python blenderMcp/runHeadless.py"
)


class BlenderError(RuntimeError):
    """Blender is unreachable or reported a failed command."""

    def __init__(self, message: str, code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


class BlenderNotRunning(BlenderError):
    """Nothing is listening on the configured host and port."""


class BlenderConnection:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        token: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.token = token

    def send_command(self, command: str, params: Optional[dict] = None,
                     timeout: Optional[float] = None) -> Any:
        """Send one command and return its result, or raise BlenderError."""
        wait = self.timeout if timeout is None else timeout
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "type": command, "params": params or {}, "timeout": wait}
        if self.token:
            request["token"] = self.token
        payload = json.dumps(request).encode("utf-8") + b"\n"

        try:
            with socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT) as sock:
                sock.settimeout(wait + RESPONSE_GRACE)
                sock.sendall(payload)
                response = _read_response(sock)
        except ConnectionRefusedError as exc:
            raise BlenderNotRunning(
                f"Could not reach the Blender bridge at {self.host}:{self.port}. {NOT_RUNNING_HINT}",
                code="not_running",
            ) from exc
        except socket.timeout as exc:
            raise BlenderError(
                f"Blender did not answer within {wait + RESPONSE_GRACE:.0f}s. It may be busy "
                "(rendering, a modal dialog, or a long script); try again or raise the timeout.",
                code="timeout",
            ) from exc
        except (OSError, ValueError) as exc:
            raise BlenderError(f"Blender connection failed: {exc}", code="connection") from exc

        if response.get("id") not in (None, request_id):
            raise BlenderError("Blender answered a different request", code="protocol")
        if response.get("status") != "success":
            raise BlenderError(
                response.get("message") or "Blender reported an unknown error",
                code=response.get("code"),
            )
        return response.get("result")


def _read_response(sock: socket.socket) -> dict:
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = sock.recv(1024 * 1024)
        if not chunk:
            if buffer:
                break
            raise ConnectionError("Blender closed the connection without answering")
        buffer += chunk
        if len(buffer) > MAX_RESPONSE_BYTES:
            raise ValueError("response from Blender exceeds the size limit")

    line = bytes(buffer).split(b"\n", 1)[0]
    response = json.loads(line.decode("utf-8"))
    if not isinstance(response, dict):
        raise ValueError("response from Blender is not a JSON object")
    return response
