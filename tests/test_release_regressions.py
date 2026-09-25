"""Focused release-boundary regressions for the Phase 3 extension."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from ai_3d_generator.core.capabilities import ProviderCapabilities
from ai_3d_generator.core.config import validate_config
from ai_3d_generator.core.errors import NetworkError, ValidationError
from ai_3d_generator.core.models import ProviderConfig
from ai_3d_generator.providers.custom_rest import CustomRESTProvider
from ai_3d_generator.services.history import make_entry, read_history, write_history
from ai_3d_generator.utils.http import HttpClient


def test_capability_flags_must_agree_with_declared_modes() -> None:
    with pytest.raises(ValueError):
        ProviderCapabilities(
            provider_id="broken",
            name="Broken",
            supported_generation_modes=("text_to_3d",),
            supports_text_to_3d=False,
        )
    with pytest.raises(ValueError):
        ProviderCapabilities(
            provider_id="broken",
            name="Broken",
            supported_generation_modes=("image_to_3d",),
            supports_image_to_3d=False,
        )


def test_capability_formats_and_modes_are_restricted() -> None:
    with pytest.raises(ValueError):
        ProviderCapabilities(provider_id="broken", name="Broken", supported_formats=("exe",))
    with pytest.raises(ValueError):
        ProviderCapabilities(
            provider_id="broken",
            name="Broken",
            supported_generation_modes=("image_to_3d",),
            supports_text_to_3d=True,
        )


def test_config_rejects_unbounded_timing_values() -> None:
    for key in ("timeout", "poll_interval", "job_timeout"):
        values = validate_config({
            "provider": "mock",
            "prompt": "chair",
            "timeout": 60.0,
            "poll_interval": 2.0,
            "job_timeout": 900.0,
            key: 1e300,
        })
        assert values, key


def test_custom_rest_download_rejects_non_origin_url() -> None:
    class Client:
        timeout = 60.0

        def request(self, *_args, **_kwargs):
            raise AssertionError("network must not be reached")

    provider = CustomRESTProvider(
        ProviderConfig(base_url="https://api.example.test"),
        client=Client(),
    )
    with pytest.raises(ValidationError):
        provider.download_asset("http://api.example.test:8443/asset.glb", Path("/tmp/asset.glb"))


def test_history_rejects_external_or_invalid_enum_fields(tmp_path: Path) -> None:
    external = tmp_path.parent / "outside.glb"
    external.write_bytes(b"not a glb")
    path = tmp_path / "history.json"
    path.write_text(
        __import__("json").dumps([{
            "job_id": "job-1", "timestamp": "now", "prompt": "chair",
            "negative_prompt": "", "provider": "mock", "model": "default",
            "quality": "not-a-quality", "output_format": "exe",
            "file_path": str(external), "status": "failed",
        }]),
        encoding="utf-8",
    )
    assert read_history(path) == []


def test_history_migrates_minimal_legacy_row_and_preserves_contained_asset(tmp_path: Path) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_text(
        '[{"job_id":"legacy","prompt":"chair","provider":"mock","status":"completed"}]',
        encoding="utf-8",
    )
    migrated = read_history(history_path)
    assert len(migrated) == 1
    assert migrated[0].job_id == "legacy"
    assert migrated[0].model == "default"
    assert migrated[0].quality == "standard"
    assert migrated[0].output_format == "glb"

    asset = tmp_path / "job" / "asset.glb"
    asset.parent.mkdir()
    asset.write_bytes(b"fixture")
    entry = make_entry(
        job_id="contained",
        prompt="chair",
        negative_prompt="",
        provider="mock",
        model="default",
        quality="standard",
        output_format="glb",
        file_path=str(asset),
        status="completed",
    )
    write_history(history_path, [entry])
    restored = read_history(history_path)
    assert len(restored) == 1
    assert restored[0].file_path == str(asset.resolve())


def test_http_redirect_origin_policy_blocks_different_port() -> None:
    target_url = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", target_url["url"])
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    source_thread = threading.Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        target_url["url"] = f"http://127.0.0.1:{target.server_port}/final"
        client = HttpClient(allowed_redirect_origins=[f"http://127.0.0.1:{source.server_port}"])
        with pytest.raises(NetworkError, match="allowlist|origin"):
            client.request("GET", f"http://127.0.0.1:{source.server_port}/start")
    finally:
        for server, thread in ((source, source_thread), (target, target_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
