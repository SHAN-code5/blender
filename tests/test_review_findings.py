"""Regressions for the review findings (library durability, endpoint join, download detection)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_3d_generator.core.errors import ProviderResponseError, ValidationError
from ai_3d_generator.core.models import GenerationRequest
from ai_3d_generator.providers.mock import MockProvider
from ai_3d_generator.services.download_manager import DownloadManager, detect_format
from ai_3d_generator.services.job_manager import JobManager
from ai_3d_generator.services.library_service import AssetLibrary
from ai_3d_generator.utils.http import HttpResponse
from ai_3d_generator.utils.paths import join_endpoint


def _persisted_ids(library: AssetLibrary) -> list[str]:
    payload = json.loads(library.metadata_path.read_text(encoding="utf-8"))
    return sorted(row["id"] for row in payload["assets"])


def _add(library: AssetLibrary, source_dir: Path, asset_id: str) -> None:
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


# --- utils.paths.join_endpoint -------------------------------------------------


def test_join_endpoint_preserves_configured_base_path() -> None:
    assert join_endpoint("https://api.example.test/v1", "/generate") == "https://api.example.test/v1/generate"
    assert join_endpoint("https://api.example.test/v1/", "/jobs/abc") == "https://api.example.test/v1/jobs/abc"
    assert join_endpoint("https://api.example.test", "/generate") == "https://api.example.test/generate"


def test_join_endpoint_still_rejects_cross_origin_targets() -> None:
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/v1", "https://evil.example.test/generate")
    with pytest.raises(ValidationError):
        join_endpoint("https://api.example.test/v1", "http://169.254.169.254/latest")


# --- services.download_manager.detect_format -----------------------------------


def test_detect_format_falls_back_when_url_suffix_is_not_an_asset() -> None:
    assert detect_format("https://cdn.example.test/download.php", "glb", "model/gltf-binary") == "glb"
    assert detect_format("https://cdn.example.test/download.php", "glb") == "glb"
    assert detect_format("https://cdn.example.test/result?id=1", "", "model/gltf-binary") == "glb"
    # With no other signal the unsupported suffix is still reported rather than guessed.
    with pytest.raises((ValidationError, ProviderResponseError)):
        detect_format("https://cdn.example.test/download.php", "")


def test_download_manager_accepts_non_asset_url_suffix(tmp_path: Path) -> None:
    body = b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little")

    class Client:
        timeout = 5.0

        def set_allowed_redirect_origins(self, origins):  # noqa: ANN001 - test stub
            self.origins = set(origins)

        def request(self, method, url, **kwargs):  # noqa: ANN001 - test stub
            return HttpResponse(status=200, headers={"Content-Type": "model/gltf-binary"}, body=body)

    manager = DownloadManager(tmp_path, client=Client())
    asset = manager.download("https://cdn.example.test/download.php", "job-1", "glb")
    assert asset.format == "glb"
    assert asset.path.is_file()


# --- services.job_manager terminal progress ------------------------------------


def test_failed_job_does_not_report_full_progress(tmp_path: Path) -> None:
    provider = MockProvider()
    job = JobManager(provider, DownloadManager(tmp_path))
    job.start(GenerationRequest(prompt="mock-fail chair").to_dict())
    for _ in range(8):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "failed"
    assert 0.0 < job.snapshot.progress < 1.0


# --- services.library_service durability ---------------------------------------


def test_library_keeps_metadata_when_file_is_temporarily_unavailable(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    _add(library, sources, "a")
    _add(library, sources, "b")

    a_path = next(entry.file for entry in library.list_assets() if entry.id == "a")
    Path(a_path).rename(a_path + ".away")

    # The unavailable asset is hidden from the display list...
    assert [entry.id for entry in library.list_assets()] == ["b"]

    # ...but an unrelated write must not permanently prune its metadata.
    library.update("b", favorite=True)
    assert _persisted_ids(library) == ["a", "b"]

    _add(library, sources, "d")
    assert _persisted_ids(library) == ["a", "b", "d"]

    # Once the file is back, the record reappears.
    Path(a_path + ".away").rename(a_path)
    assert sorted(entry.id for entry in library.list_assets()) == ["a", "b", "d"]


def test_library_add_does_not_prune_unavailable_rows(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    _add(library, sources, "a")
    _add(library, sources, "b")

    a_path = next(entry.file for entry in library.list_assets() if entry.id == "a")
    Path(a_path).rename(a_path + ".away")

    _add(library, sources, "c")
    assert _persisted_ids(library) == ["a", "b", "c"]


def test_library_accepts_generator_tags_in_a_single_pass(tmp_path: Path) -> None:
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


def test_library_still_drops_rows_that_escape_the_asset_directory(tmp_path: Path) -> None:
    library = AssetLibrary(tmp_path)
    sources = tmp_path / "sources"
    sources.mkdir()
    _add(library, sources, "valid")

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
    assert _persisted_ids(library) == ["valid"]
    assert [entry.id for entry in library.list_assets()] == ["valid"]
