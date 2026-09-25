"""Tests for provider/job/download behavior that do not require Blender."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_3d_generator.core.errors import DownloadError, ProviderResponseError
from ai_3d_generator.core.capabilities import ProviderCapabilities
from ai_3d_generator.core.models import ProviderConfig
from ai_3d_generator.providers.custom_rest import CustomRESTProvider
from ai_3d_generator.providers.mock import MockProvider
from ai_3d_generator.services.download_manager import DownloadManager, detect_format
from ai_3d_generator.services.job_manager import JobManager


class FakeHttpResponse:
    def __init__(self, body: bytes, content_type: str = ""):
        self.body = body
        self.headers = {"Content-Type": content_type} if content_type else {}


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_mock_job_manager_downloads_fixture(tmp_path):
    provider = MockProvider()
    manager = DownloadManager(tmp_path, allowed_hosts=[])

    class FixtureDownload(DownloadManager):
        def download(self, url, job_id, requested_format="", provider=None):
            # The manager is replaced to make the fixture path observable while
            # the real format checks remain covered separately.
            return super().download(url, job_id, requested_format, provider)

    job = JobManager(provider, FixtureDownload(tmp_path), job_timeout=30)
    finished = []
    job.on_finished = lambda result, snapshot: finished.append(result)
    job.start({"prompt": "chair", "output_format": "glb", "quality": "draft"})
    for _ in range(6):
        if job.snapshot.is_finished:
            break
        job.tick()
    assert job.snapshot.state == "completed"
    assert job.snapshot.output_path.endswith(".glb")
    assert finished and finished[0].status == "completed"
    assert Path(job.snapshot.output_path).is_file()


def test_mock_provider_cancel_is_observable():
    provider = MockProvider()
    job = provider.create_generation_job({"prompt": "chair"})
    provider.cancel_job(job.job_id)
    assert provider.get_job_status(job.job_id).state == "cancelled"


def test_download_manager_rejects_unknown_host_when_allowlist_is_set(tmp_path):
    manager = DownloadManager(tmp_path, allowed_hosts=["cdn.example.test"])
    with pytest.raises(DownloadError):
        manager.download("https://evil.example/a.glb", "job-1", "glb")


def test_download_manager_rejects_cross_host_authenticated_download(tmp_path):
    class Client:
        def request(self, method, url, **kwargs):
            raise AssertionError("network must not be reached")

    provider = CustomRESTProvider(ProviderConfig(base_url="https://api.example.test"), client=Client())
    manager = DownloadManager(tmp_path, client=Client())
    with pytest.raises(DownloadError, match="Authenticated asset downloads"):
        manager.download("https://cdn.example.test/a.glb", "job-1", "glb", provider)


def test_custom_rest_capabilities_are_explicit() -> None:
    capabilities = CustomRESTProvider().capabilities()
    assert capabilities.supports_generation_mode("text_to_3d")
    assert not capabilities.supports_generation_mode("image_to_3d")
    assert capabilities.supported_formats == ("glb",)
    assert capabilities.supports_cancellation is True


def test_custom_rest_capability_reflects_disabled_cancel_endpoint() -> None:
    capabilities = CustomRESTProvider(ProviderConfig(cancel_path="")).capabilities()
    assert capabilities.supports_cancellation is False
    assert capabilities.supported_formats_for_mode("image_to_3d") == ()


def test_custom_rest_disabled_cancel_is_not_advertised() -> None:
    capabilities = CustomRESTProvider(ProviderConfig(cancel_path="")).capabilities()
    assert capabilities.supports_cancellation is False
    with pytest.raises(NotImplementedError):
        CustomRESTProvider(ProviderConfig(cancel_path="")).cancel_job("job-1")


def test_capability_flags_cannot_disagree_with_modes() -> None:
    with pytest.raises(ValueError):
        ProviderCapabilities(
            provider_id="broken",
            name="Broken",
            supported_generation_modes=("image_to_3d",),
            supports_image_to_3d=False,
        )

    class Client:
        def request_json(self, method, url, **kwargs):
            assert method == "POST"
            assert url == "https://example.test/generate"
            assert kwargs["json_body"]["prompt"] == "chair"
            return {"data": {"id": "abc-1"}}, object()

        def request(self, method, url, **kwargs):
            return FakeHttpResponse(b"", "application/json")

    provider = CustomRESTProvider(
        ProviderConfig(base_url="https://example.test", model="m", job_id_path="data.id"),
        client=Client(),
    )
    handle = provider.create_generation_job({"prompt": "chair"})
    assert handle.job_id == "abc-1"


def test_detect_format_prefers_url_extension():
    assert detect_format("https://cdn.example.test/download", "glb") == "glb"
