"""Shared helpers for the Blender MCP tests: a local fake HTTP API and MCP client plumbing."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest


class _Handler(BaseHTTPRequestHandler):
    def _serve(self, method):
        state = self.server.state
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        state.requests.append(SimpleNamespace(method=method, path=parsed.path, query=parse_qs(parsed.query),
                                              headers={k.lower(): v for k, v in self.headers.items()}, body=body))
        status, payload, headers = state.routes.get((method, parsed.path), (404, {"message": "no route"}, {}))
        if callable(payload):
            payload = payload(SimpleNamespace(query=parse_qs(parsed.query), body=body))
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - http.server naming
        self._serve("GET")

    def do_POST(self):  # noqa: N802
        self._serve("POST")

    def log_message(self, *args):
        pass


@pytest.fixture
def http_api():
    """A local HTTP server; register responses with ``add(method, path, payload, status)``."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    state = SimpleNamespace(routes={}, requests=[])
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def add(method, path, payload, status=200, headers=None):
        state.routes[(method, path)] = (status, payload, headers or {})

    def add_file(path, payload):
        add("GET", path, payload)
        return {"url": base + path, "md5": hashlib.md5(payload).hexdigest(), "size": len(payload)}

    def hits(path):
        return sum(1 for r in state.requests if r.path == path)

    try:
        yield SimpleNamespace(base=base, add=add, add_file=add_file, requests=state.requests, hits=hits)
    finally:
        server.shutdown()
        server.server_close()


class RecordingConnection:
    """Stands in for BlenderConnection: records commands and answers like the add-on would."""

    def __init__(self, results=None, error=None, shared_files=True, failures=None):
        self.calls = []
        self.results = results or {}
        self.error = error
        self.shared_files = shared_files
        self.failures = dict(failures or {})  # command -> exception raised once
        self.received = {}

    def send_command(self, command, params=None, timeout=None):
        self.calls.append((command, params, timeout))
        if self.error:
            raise self.error
        if command in self.failures:
            raise self.failures.pop(command)
        if command == "stat_file":
            path = params["filepath"]
            exists = self.shared_files and os.path.isfile(path)
            return {"filepath": path, "exists": exists, "size": os.path.getsize(path) if exists else None}
        if command == "receive_file":
            key = (params["transfer_id"], params["relpath"])
            import base64

            data = base64.b64decode(params["data_base64"])
            current = self.received.get(key, b"") if params["offset"] else b""
            assert len(current) == params["offset"]
            self.received[key] = current + data
            return {"filepath": f"/remote/{params['transfer_id']}/{params['relpath']}", "size": len(self.received[key])}
        result = self.results.get(command, {"command": command})
        return result(params) if callable(result) else result

    def commands(self):
        return [call[0] for call in self.calls]


@contextlib.asynccontextmanager
async def mcp_client(server):
    from blenderMcp import compat

    if compat.SDK_MAJOR >= 2:
        from mcp.client import Client

        async with Client(server) as client:
            yield client
    else:
        from mcp.shared.memory import create_connected_server_and_client_session

        async with create_connected_server_and_client_session(server._mcp_server) as session:
            yield session


def is_error(result) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


def run_client(server, script):
    """Run ``await script(client)`` against the server over an in-memory MCP session."""
    import anyio

    async def go():
        async with mcp_client(server) as client:
            return await script(client)

    return anyio.run(go)


def run_tools(server, calls):
    async def script(client):
        tools = (await client.list_tools()).tools
        results = [await client.call_tool(name, args) for name, args in calls]
        return tools, results

    return run_client(server, script)


def text_of(result) -> str:
    return result.content[0].text
