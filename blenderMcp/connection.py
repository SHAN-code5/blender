"""TCP client that forwards MCP tool calls to the Blender add-on.

Protocol: one JSON object per line.

    request  -> {"type": "<command>", "params": {...}}
    response -> {"status": "success", "result": ...}
                {"status": "error", "message": "..."}
"""

import json
import socket
from typing import Any, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class BlenderError(RuntimeError):
    """Raised when Blender is unreachable or reports a failed command."""


class BlenderConnection:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._buffer = b""

    def send_command(self, command_type: str, params: Optional[dict] = None) -> Any:
        """Send one command and return its result, or raise BlenderError."""
        payload = json.dumps({"type": command_type, "params": params or {}}).encode("utf-8")
        try:
            sock = self._connect()
            sock.sendall(payload + b"\n")
            response = self._read_response(sock)
        except (OSError, ValueError) as exc:
            # Drop the socket so the next call reconnects from a clean state.
            self.close()
            raise BlenderError(f"Blender connection failed: {exc}") from exc

        if response.get("status") != "success":
            raise BlenderError(response.get("message") or "Blender reported an unknown error")
        return response.get("result")

    def _connect(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._buffer = b""
        return self._sock

    def _read_response(self, sock: socket.socket) -> dict:
        while b"\n" not in self._buffer:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("Blender closed the connection")
            self._buffer += chunk
            if len(self._buffer) > MAX_RESPONSE_BYTES:
                raise ValueError("response from Blender exceeds the size limit")

        line, _, self._buffer = self._buffer.partition(b"\n")
        response = json.loads(line.decode("utf-8"))
        if not isinstance(response, dict):
            raise ValueError("response from Blender is not a JSON object")
        return response
