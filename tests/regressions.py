"""Regression tests for validation, security boundaries, and library durability."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from ai3dgenerator.core import config
from ai3dgenerator.core.errors import DownloadError, ProviderResponseError, ValidationError
from ai3dgenerator.core.models import GenerationRequest, ProviderConfig
from ai3dgenerator.providers.customRest import CustomRESTProvider
from ai3dgenerator.providers.mock import MockProvider
from ai3dgenerator.services.downloadManager import DownloadManager, detect_format, validate_asset_file
from ai3dgenerator.services.history import make_entry, read_history, write_history
from ai3dgenerator.services.jobManager import JobManager
from ai3dgenerator.services.libraryService import AssetLibrary
from ai3dgenerator.utils import files, validation
from ai3dgenerator.utils.http import HttpClient, HttpResponse
from ai3dgenerator.utils.paths import join_endpoint


def checkCustomProviderValidatesUrlBeforeNetwork() -> None:
    provider = CustomRESTProvider(ProviderConfig(base_url="file:///tmp/service", requires_api_key=False))
    with pytest.raises(ValidationError):
        provider.validate_credentials()


def checkCustomProviderRequiresKeyOnlyWhenConfigured() -> None:
    provider = CustomRESTProvider(ProviderConfig(base_url="https://example.test", requires_api_key=True, api_key_env="MISSING_AI3D_TEST_KEY"))
    with pytest.raises(Exception, match="API key"):
        provider.validate_credentials()


def checkHttpUrlRejectsControlCharactersAndFragments() -> None:
    with pytest.raises(ValidationError):
        validation.validate_http_url("https://example.test/\nfile", "URL")
    with pytest.raises(ValidationError):
        validation.validate_http_url("https://example.test/#fragment", "URL")


def checkGlbIntegrityCheckRejectsTruncatedFile(tmp_path: Path) -> None:
    path = tmp_path / "bad.glb"
    path.write_bytes(b"glTF" + (2).to_bytes(4, "little") + (100).to_bytes(4, "little"))
    with pytest.raises(Exception):
        validate_asset_file(path, "glb")


def checkVisibleHistoryIndexMapsNewestTwenty(tmp_path: Path) -> None:
    from ai3dgenerator.services.history import visible_history_index_to_storage

    rows = [make_entry(job_id=str(index), prompt="chair", negative_prompt="", provider="mock", model="default", quality="standard", output_format="glb") for index in range(21)]
    assert visible_history_index_to_storage(rows, 0) == 1
    assert visible_history_index_to_storage(rows, 19) == 20


def checkHistoryRejectsMalformedTypedRows(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    row = {
        "job_id": "bad", "timestamp": "now", "prompt": "chair", "negative_prompt": "",
        "provider": "mock", "model": "default", "quality": "standard", "output_format": "glb",
        "file_path": "", "status": "completed", "polygon_target": "not-int",
    }
    path.write_text(__import__("json").dumps([row]), encoding="utf-8")
    assert read_history(path) == []


def checkHistoryIgnoresCorruptRowsAndStaysSecretFree(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text('[{"job_id":"ok","prompt":"chair","provider":"mock","status":"completed"}, "not-an-entry", {"job_id":"bad","prompt":"x","provider":"mock","api_key":"secret","status":"failed"}]', encoding="utf-8")
    entries = read_history(path)
    assert len(entries) == 1
    assert entries[0].job_id == "ok"
    assert "api_key" not in write_history(path, entries).read_text(encoding="utf-8")


def checkMockFailureDoesNotComplete(tmp_path: Path) -> None:
    provider = MockProvider()
    job = provider.create_generation_job({"prompt": "mock-fail chair"})
    assert provider.get_job_status(job.job_id).state == "queued"
    assert provider.get_job_status(job.job_id).state == "processing"
    assert provider.get_job_status(job.job_id).state == "failed"


def checkMockJobCancelsBeforeSubmissionCallback(tmp_path: Path) -> None:
    provider = MockProvider()
    manager = DownloadManager(tmp_path)
    job = __import__("ai3dgenerator.services.jobManager", fromlist=["JobManager"]).JobManager(provider, manager)
    job.start({"prompt": "chair", "output_format": "glb"})
    job.cancel()
    job.tick()
    assert job.snapshot.state == "cancelled"


def checkSafeOutputPathRejectsSymlinkEscape(tmp_path: Path) -> None:
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


def checkConfigRejectsNonFiniteTimeouts() -> None:
    cfg = config.default_config()
    cfg["generation_mode"] = "imageTo3d"
    for key in ("timeout", "poll_interval", "job_timeout"):
        bad = config.default_config()
        bad["generation_mode"] = "imageTo3d"
        bad[key] = float("nan")
        assert config.validate_config(bad), key


def checkConfigAcceptsMockWithoutPrompt() -> None:
    cfg = config.default_config()
    cfg["generation_mode"] = "imageTo3d"
    assert config.validate_config(cfg) == []


def checkConfiguredAuthHeaderIsNotForwardedOnRedirect() -> None:
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


def checkAuthHeadersAreDroppedOnRedirect() -> None:
    """Standard auth headers are dropped when a request is redirected."""
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


def checkDownloadAllowlistRejectsRedirectedHost(tmp_path: Path) -> None:
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
            self.wfile.write((Path(__file__).parent.parent / "ai3dgenerator/fixtures/mockAsset.glb").read_bytes())

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


def checkCustomProviderRejectsCrossOriginEndpoints() -> None:
    with pytest.raises(Exception):
        CustomRESTProvider(
            ProviderConfig(base_url="https://api.example.test", generate_path="https://evil.example.test/generate")
        ).validate_credentials()
    with pytest.raises(Exception):
        CustomRESTProvider(
            ProviderConfig(base_url="https://api.example.test", status_path="http://169.254.169.254/latest")
        ).validate_credentials()


def checkEndpointTemplateCannotEscapeConfiguredProviderHost() -> None:
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/root", "https://evil.api.example.test/x")
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/root", "http://169.254.169.254/latest/meta-data")


def checkValidateConfigAlwaysReturnsAListAndFlagsUnknownProvider() -> None:
    base = {
        "generation_mode": "imageTo3d",
        "timeout": 60.0,
        "poll_interval": 2.0,
        "job_timeout": 900.0,
    }
    unknown = config.validate_config({**base, "provider": "not-a-provider"})
    assert isinstance(unknown, list) and unknown, "unknown provider must produce errors"
    missing = config.validate_config({**base, "provider": ""})
    assert isinstance(missing, list) and any("provider" in error.lower() for error in missing)


def checkContentDispositionPreservesFilenameCase() -> None:
    assert files.format_content_disposition('attachment; FileName="MyAsset.GLB"') == "MyAsset.GLB"
    assert files.format_content_disposition("inline") is None


def checkDownloadManagerDoesNotWidenSuppliedClientRedirectPolicy(tmp_path: Path) -> None:
    calls = []

    class _Client:
        timeout = 5.0

        def set_allowed_redirect_origins(self, origins):
            calls.append(set(origins))

    manager = DownloadManager(tmp_path, client=_Client())
    assert calls == [], "an empty origin set must not be applied to a supplied client"


# --- utils.paths.join_endpoint -------------------------------------------------


def checkJoinEndpointPreservesConfiguredBasePath() -> None:
    assert join_endpoint("https://api.example.test/v1", "/generate") == "https://api.example.test/v1/generate"
    assert join_endpoint("https://api.example.test/v1/", "/jobs/abc") == "https://api.example.test/v1/jobs/abc"
    assert join_endpoint("https://api.example.test", "/generate") == "https://api.example.test/generate"


def checkJoinEndpointStillRejectsCrossOriginTargets() -> None:
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/v1", "https://evil.example.test/generate")
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/v1", "http://169.254.169.254/latest")


# --- services.downloadManager.detect_format -----------------------------------


def checkDetectFormatFallsBackWhenUrlSuffixIsNotAnAsset() -> None:
    assert detect_format("https://cdn.example.test/download.php", "glb", "model/gltf-binary") == "glb"
    assert detect_format("https://cdn.example.test/download.php", "glb") == "glb"
    assert detect_format("https://cdn.example.test/result?id=1", "", "model/gltf-binary") == "glb"
    # With no other signal the unsupported suffix is still reported rather than guessed.
    with pytest.raises((ValidationError, ProviderResponseError)):
        detect_format("https://cdn.example.test/download.php", "")


def checkDownloadManagerAcceptsNonAssetUrlSuffix(tmp_path: Path) -> None:
    body = b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little")

    class Client:
        timeout = 5.0

        def set_allowed_redirect_origins(self, origins):
            self.origins = set(origins)

        def request(self, method, url, **kwargs):
            return HttpResponse(status=200, headers={"Content-Type": "model/gltf-binary"}, body=body)

    manager = DownloadManager(tmp_path, client=Client())
    asset = manager.download("https://cdn.example.test/download.php", "job-1", "glb")
    assert asset.format == "glb"
    assert asset.path.is_file()


# --- services.jobManager terminal progress ------------------------------------


def checkFailedJobDoesNotReportFullProgress(tmp_path: Path) -> None:
    provider = MockProvider()
    job = JobManager(provider, DownloadManager(tmp_path))
    job.start(GenerationRequest(prompt="mock-fail chair").to_dict())
    for _ in range(8):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "failed"
    assert 0.0 < job.snapshot.progress < 1.0


# --- services.libraryService durability ---------------------------------------


def persistedIds(library: AssetLibrary) -> list[str]:
    payload = json.loads(library.metadata_path.read_text(encoding="utf-8"))
    return sorted(row["id"] for row in payload["assets"])


def addLibraryAsset(library: AssetLibrary, source_dir: Path, asset_id: str) -> None:
    source = source_dir / f"{asset_id}.glb"
    source.write_bytes(b"fixture")
    library.add_asset(
        asset_id=asset_id,
        prompt=asset_id,
        provider="mock",
        model="default",
        output_format="glb",
        file_path=source,
        copy_file=True,
    )


def checkLibraryKeepsMetadataWhenFileIsTemporarilyUnavailable(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    addLibraryAsset(library, sources, "a")
    addLibraryAsset(library, sources, "b")

    a_path = next(entry.file for entry in library.list_assets() if entry.id == "a")
    Path(a_path).rename(a_path + ".away")

    # The unavailable asset is hidden from the display list...
    assert [entry.id for entry in library.list_assets()] == ["b"]

    # ...but an unrelated write must not permanently prune its metadata.
    library.update("b", favorite=True)
    assert persistedIds(library) == ["a", "b"]

    addLibraryAsset(library, sources, "d")
    assert persistedIds(library) == ["a", "b", "d"]

    # Once the file is back, the record reappears.
    Path(a_path + ".away").rename(a_path)
    assert sorted(entry.id for entry in library.list_assets()) == ["a", "b", "d"]


def checkLibraryAddDoesNotPruneUnavailableRows(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    addLibraryAsset(library, sources, "a")
    addLibraryAsset(library, sources, "b")

    a_path = next(entry.file for entry in library.list_assets() if entry.id == "a")
    Path(a_path).rename(a_path + ".away")

    addLibraryAsset(library, sources, "c")
    assert persistedIds(library) == ["a", "b", "c"]


def checkLibraryAcceptsGeneratorTagsInASinglePass(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    source = sources / "a.glb"
    source.write_bytes(b"fixture")
    library.add_asset(
        asset_id="a",
        prompt="a",
        provider="mock",
        model="default",
        output_format="glb",
        file_path=source,
        copy_file=True,
        tags=(tag for tag in ("Chair", "Wood")),
    )
    assert library.list_assets()[0].tags == ["chair", "wood"]


def checkLibraryStillDropsRowsThatEscapeTheAssetDirectory(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    addLibraryAsset(library, sources, "valid")

    external = tmp_path / "external.glb"
    external.write_bytes(b"external")
    payload = {
        "schema_version": 1,
        "assets": [
            {
                "id": "valid",
                "prompt": "valid",
                "provider": "mock",
                "model": "m",
                "created_at": "now",
                "format": "glb",
                "file": next(entry.file for entry in library.list_assets()),
                "thumbnail": "",
            },
            {
                "id": "escape",
                "prompt": "escape",
                "provider": "mock",
                "model": "m",
                "created_at": "now",
                "format": "glb",
                "file": str(external),
                "thumbnail": "",
            },
        ],
    }
    library.metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    library.update("valid", favorite=True)
    assert persistedIds(library) == ["valid"]
    assert [entry.id for entry in library.list_assets()] == ["valid"]
