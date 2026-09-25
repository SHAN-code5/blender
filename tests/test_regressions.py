from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import threading
from threading import Thread

import pytest

from ai_3d_generator.core import config
from ai_3d_generator.core.errors import DownloadError, ValidationError
from ai_3d_generator.core.models import ProviderConfig
from ai_3d_generator.providers.custom_rest import CustomRESTProvider
from ai_3d_generator.providers.mock import MockProvider
from ai_3d_generator.services.download_manager import DownloadManager, validate_asset_file
from ai_3d_generator.services.history import make_entry, read_history, write_history
from ai_3d_generator.utils import files, validation
from ai_3d_generator.utils.http import HttpClient
from ai_3d_generator.utils.paths import join_endpoint


def test_custom_provider_validates_url_before_network() -> None:
    provider = CustomRESTProvider(ProviderConfig(base_url="file:///tmp/service", requires_api_key=False))
    with pytest.raises(ValidationError):
        provider.validate_credentials()


def test_custom_provider_requires_key_only_when_configured() -> None:
    provider = CustomRESTProvider(ProviderConfig(base_url="https://example.test", requires_api_key=True, api_key_env="MISSING_AI3D_TEST_KEY"))
    with pytest.raises(Exception, match="API key"):
        provider.validate_credentials()


def test_http_url_rejects_control_characters_and_fragments() -> None:
    with pytest.raises(ValidationError):
        validation.validate_http_url("https://example.test/\nfile", "URL")
    with pytest.raises(ValidationError):
        validation.validate_http_url("https://example.test/#fragment", "URL")


def test_glb_integrity_check_rejects_truncated_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.glb"
    path.write_bytes(b"glTF" + (2).to_bytes(4, "little") + (100).to_bytes(4, "little"))
    with pytest.raises(Exception):
        validate_asset_file(path, "glb")


def test_visible_history_index_maps_newest_twenty(tmp_path: Path) -> None:
    from ai_3d_generator.services.history import visible_history_index_to_storage

    rows = [make_entry(job_id=str(index), prompt="chair", negative_prompt="", provider="mock", model="default", quality="standard", output_format="glb") for index in range(21)]
    assert visible_history_index_to_storage(rows, 0) == 1
    assert visible_history_index_to_storage(rows, 19) == 20


def test_history_rejects_malformed_typed_rows(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    row = {
        "job_id": "bad", "timestamp": "now", "prompt": "chair", "negative_prompt": "",
        "provider": "mock", "model": "default", "quality": "standard", "output_format": "glb",
        "file_path": "", "status": "completed", "polygon_target": "not-int",
    }
    path.write_text(__import__("json").dumps([row]), encoding="utf-8")
    assert read_history(path) == []


def test_history_ignores_corrupt_rows_and_stays_secret_free(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text('[{"job_id":"ok","prompt":"chair","provider":"mock","status":"completed"}, "not-an-entry", {"job_id":"bad","prompt":"x","provider":"mock","api_key":"secret","status":"failed"}]', encoding="utf-8")
    entries = read_history(path)
    assert len(entries) == 1
    assert entries[0].job_id == "ok"
    assert "api_key" not in write_history(path, entries).read_text(encoding="utf-8")


def test_mock_failure_does_not_complete(tmp_path: Path) -> None:
    provider = MockProvider()
    job = provider.create_generation_job({"prompt": "mock-fail chair"})
    assert provider.get_job_status(job.job_id).state == "queued"
    assert provider.get_job_status(job.job_id).state == "processing"
    assert provider.get_job_status(job.job_id).state == "failed"


def test_mock_job_cancels_before_submission_callback(tmp_path: Path) -> None:
    provider = MockProvider()
    manager = DownloadManager(tmp_path)
    job = __import__("ai_3d_generator.services.job_manager", fromlist=["JobManager"]).JobManager(provider, manager)
    job.start({"prompt": "chair", "output_format": "glb"})
    job.cancel()
    job.tick()
    assert job.snapshot.state == "cancelled"


def test_safe_output_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValidationError):
        files.safe_output_path(root, "job", "../link/asset", "glb")


def test_config_rejects_non_finite_timeouts() -> None:
    cfg = config.default_config()
    cfg["generation_mode"] = "image_to_3d"
    for key in ("timeout", "poll_interval", "job_timeout"):
        bad = config.default_config()
        bad["generation_mode"] = "image_to_3d"
        bad[key] = float("nan")
        assert config.validate_config(bad), key


def test_config_accepts_mock_without_prompt() -> None:
    cfg = config.default_config()
    cfg["generation_mode"] = "image_to_3d"
    assert config.validate_config(cfg) == []


def test_configured_auth_header_is_not_forwarded_on_redirect() -> None:
    """A provider-specific auth header is dropped along with standard auth."""
    seen = []
    target = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("X-Vendor-Auth"))
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", target["url"])
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

        def log_message(self, *_args):
            return

    target_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    target_thread = Thread(target=target_server.serve_forever, daemon=True)
    target_thread.start()
    target["url"] = f"http://127.0.0.1:{target_server.server_port}/final"
    start_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    start_thread = Thread(target=start_server.serve_forever, daemon=True)
    start_thread.start()
    try:
        response = HttpClient().request(
            "GET",
            f"http://127.0.0.1:{start_server.server_port}/start",
            headers={"X-Vendor-Auth": "vendor-secret"},
        )
        assert response.status == 200
        assert seen == ["vendor-secret", None]
    finally:
        start_server.shutdown()
        target_server.shutdown()
        start_server.server_close()
        target_server.server_close()


    seen = []
    target_url = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization"), self.headers.get("X-Api-Key"), self.headers.get("X-Vendor-Auth")))
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

    def start_server(handler):
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    target, target_thread = start_server(Handler)
    source, source_thread = start_server(Handler)
    try:
        target_url["url"] = f"http://127.0.0.1:{target.server_port}/final"
        url = f"http://127.0.0.1:{source.server_port}/start"
        response = HttpClient().request("GET", url, headers={"Authorization": "Bearer secret", "X-Api-Key": "secret", "X-Vendor-Auth": "secret"})
        assert response.body == b"{}"
        assert ("/final", None, None, None) in seen
        assert all(auth is None and api_key is None and vendor_auth is None for path, auth, api_key, vendor_auth in seen if path == "/final")
    finally:
        for server, thread in ((source, source_thread), (target, target_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_download_allowlist_rejects_redirected_host(tmp_path: Path) -> None:
    """A redirect cannot escape the explicitly configured download host."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/start.glb":
                self.send_response(302)
                self.send_header("Location", target_url["url"])
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "model/gltf-binary")
            self.end_headers()
            self.wfile.write((Path(__file__).parent.parent / "ai_3d_generator/fixtures/mock_asset.glb").read_bytes())

        def log_message(self, *_args):
            return

    target_url = {}
    target = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    target_thread = Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    source_thread = Thread(target=source.serve_forever, daemon=True)
    source_thread.start()
    try:
        target_url["url"] = f"http://localhost:{target.server_port}/final.glb"
        manager = DownloadManager(tmp_path, allowed_hosts=["127.0.0.1"], client=HttpClient())
        with pytest.raises(DownloadError, match="allowlist"):
            manager.download(
                f"http://127.0.0.1:{source.server_port}/start.glb",
                "job-redirect",
                "glb",
            )
    finally:
        source.shutdown()
        source.server_close()
        target.shutdown()
        target.server_close()
        source_thread.join(timeout=2)
        target_thread.join(timeout=2)


def test_custom_provider_rejects_cross_origin_endpoints() -> None:
    with pytest.raises(Exception):
        CustomRESTProvider(
            ProviderConfig(base_url="https://api.example.test", generate_path="https://evil.example.test/generate")
        ).validate_credentials()
    with pytest.raises(Exception):
        CustomRESTProvider(
            ProviderConfig(base_url="https://api.example.test", status_path="http://169.254.169.254/latest")
        ).validate_credentials()


def test_endpoint_template_cannot_escape_configured_provider_host() -> None:
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/root", "https://evil.api.example.test/x")
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/root", "http://169.254.169.254/latest/meta-data")
