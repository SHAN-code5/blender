"""Tests for the Blender MCP bridge that do not require Blender or a live MCP client."""
from __future__ import annotations

import json
import socket
import socketserver
import threading

import pytest

from blenderMcp.connection import BlenderConnection, BlenderError


class _EchoHandler(socketserver.StreamRequestHandler):
    """Fake Blender: answers each request line with a canned response."""

    def handle(self):
        for raw in self.rfile:
            request = json.loads(raw)
            if request["type"] == "fail":
                response = {"status": "error", "message": "boom"}
            elif request["type"] == "garbage":
                self.wfile.write(b"not json\n")
                self.wfile.flush()
                continue
            else:
                response = {"status": "success", "result": {"echo": request}}
            self.wfile.write(json.dumps(response).encode() + b"\n")
            self.wfile.flush()


@pytest.fixture
def fake_blender():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _EchoHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def check_send_command_returns_result(fake_blender):
    connection = BlenderConnection(port=fake_blender)
    try:
        result = connection.send_command("get_scene_info", {"a": 1})
    finally:
        connection.close()
    assert result == {"echo": {"type": "get_scene_info", "params": {"a": 1}}}


def check_send_command_reuses_connection(fake_blender):
    connection = BlenderConnection(port=fake_blender)
    try:
        connection.send_command("one")
        first_socket = connection._sock
        connection.send_command("two")
        assert connection._sock is first_socket
    finally:
        connection.close()


def check_error_status_raises_with_message(fake_blender):
    connection = BlenderConnection(port=fake_blender)
    try:
        with pytest.raises(BlenderError, match="boom"):
            connection.send_command("fail")
    finally:
        connection.close()


def check_invalid_response_raises_and_resets(fake_blender):
    connection = BlenderConnection(port=fake_blender)
    try:
        with pytest.raises(BlenderError):
            connection.send_command("garbage")
        assert connection._sock is None
        # The next call reconnects cleanly.
        assert connection.send_command("ok")["echo"]["type"] == "ok"
    finally:
        connection.close()


def check_unreachable_blender_raises_blender_error():
    connection = BlenderConnection(port=_unused_port(), timeout=2)
    with pytest.raises(BlenderError, match="connection failed"):
        connection.send_command("get_scene_info")


def check_server_tools_forward_to_connection(monkeypatch):
    pytest.importorskip("mcp")
    from blenderMcp import server

    calls = []

    class RecordingConnection:
        def send_command(self, command, params=None):
            calls.append((command, params))
            return {"command": command}

    monkeypatch.setattr(server, "_connection", RecordingConnection())

    assert server.get_scene_info() == {"command": "get_scene_info"}
    server.list_objects("MESH")
    server.get_object_info("Cube")
    server.execute_blender_code("result = 1")

    assert calls == [
        ("get_scene_info", None),
        ("list_objects", {"object_type": "MESH"}),
        ("get_object_info", {"name": "Cube"}),
        ("execute_code", {"code": "result = 1"}),
    ]
