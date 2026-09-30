"""Pure-Python tests for the provider-independent core.

These tests intentionally do not import Blender so they can run in CI.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ai_3d_generator.core import config, errors, models
from ai_3d_generator.providers import registry
from ai_3d_generator.services import history
from ai_3d_generator.utils import files, validation


def test_validate_http_url_accepts_https_and_rejects_unsafe_schemes():
    assert validation.validate_http_url("https://api.example.test/generate", "API Base URL")
    with pytest.raises(errors.ValidationError):
        validation.validate_http_url("file:///tmp/secret", "API Base URL")
    with pytest.raises(errors.ValidationError):
        validation.validate_http_url("https://", "API Base URL")


def test_validate_http_url_rejects_credentials_in_url():
    with pytest.raises(errors.ValidationError):
        validation.validate_http_url("https://user:pass@example.test", "API Base URL")


def test_validate_job_id_rejects_path_traversal():
    with pytest.raises(errors.ValidationError):
        validation.validate_job_id("../../admin")
    with pytest.raises(errors.ValidationError):
        validation.validate_job_id("job/with/slash")


def test_extract_job_id_uses_configured_path():
    response = {"data": {"id": "job-123"}}
    assert validation.extract_job_id(response, "data.id") == "job-123"
    with pytest.raises(errors.ProviderResponseError):
        validation.extract_job_id({"data": {}}, "data.id")


def test_normalize_status_maps_provider_aliases():
    assert validation.normalize_status("processing") == "processing"
    assert validation.normalize_status("SUCCESS") == "completed"
    assert validation.normalize_status("queued") == "queued"
    assert validation.normalize_status("unknown") == "unknown"


def test_extract_output_url_requires_http_url():
    payload = {"result": {"asset": {"download_url": "https://cdn.example.test/a.glb"}}}
    assert validation.extract_output_url(payload, "result.asset.download_url") == "https://cdn.example.test/a.glb"
    with pytest.raises(errors.ProviderResponseError):
        validation.extract_output_url({"result": {"asset": {"download_url": "/local.glb"}}}, "result.asset.download_url")


def test_format_detection_and_path_safety():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "cache"
        target = files.safe_output_path(root, "job-1", "asset", ".glb")
        assert target.parent == root / "job-1"
        assert target.name == "asset.glb"
        with pytest.raises(errors.ValidationError):
            files.safe_output_path(root, "job-1", "../escape", ".glb")


def test_history_round_trip_omits_secrets(tmp_path):
    entry = models.HistoryEntry(
        job_id="job-1",
        timestamp="2026-01-01T00:00:00Z",
        prompt="chair",
        negative_prompt="",
        provider="mock",
        model="default",
        quality="standard",
        output_format="glb",
        file_path=str(tmp_path / "chair.glb"),
        status="completed",
    )
    path = history.write_history(tmp_path / "history.json", [entry])
    restored = history.read_history(path)
    assert restored[0].job_id == "job-1"
    assert "api_key" not in path.read_text(encoding="utf-8")


def test_mock_provider_runs_without_network():
    provider = registry.get_provider_class("mock")()
    job = provider.create_generation_job({
        "prompt": "a chair",
        "quality": "draft",
        "output_format": "glb",
    })
    assert job.job_id
    assert provider.get_job_status(job.job_id, 0).state == "queued"
    assert provider.get_job_status(job.job_id, 1).state == "processing"
    completed = provider.get_job_status(job.job_id, 2)
    assert completed.state == "completed"
    assert completed.output_url is not None


def test_config_defaults_and_validation():
    cfg = config.default_config()
    cfg["prompt"] = "a chair"
    assert cfg["output_format"] == "glb"
    assert config.validate_config(cfg) == []


def test_json_error_message_is_user_friendly():
    exc = errors.ProviderResponseError("Provider returned invalid JSON", detail="json decode failed")
    rendered = exc.user_message()
    assert "invalid json" in rendered.lower()
    assert "Traceback" not in rendered


def test_serializable_model_does_not_expose_secrets():
    item = models.GenerationRequest(prompt="chair")
    dumped = json.dumps(item.to_dict())
    assert "api_key" not in dumped
    assert "secret" not in dumped
