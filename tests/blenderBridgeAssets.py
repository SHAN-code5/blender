"""Tests for asset libraries, generation, file transfer, guides, and the update command.

External services are replaced by a local HTTP server, so the real request,
download, and checksum code runs without network access or API keys.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from blenderMcp import addonTools, cli, net, transfer
from blenderMcp.assets import AssetError, FetchedAsset, PolyHaven, PolyPizza, Sketchfab
from blenderMcp.config import Settings
from blenderMcp.connection import BlenderError

from bridgeHelpers import RecordingConnection, http_api, is_error, run_client, run_tools, text_of  # noqa: F401

GLB = b"glTF" + (2).to_bytes(4, "little") + (20).to_bytes(4, "little") + b"\x00" * 8


# --------------------------------------------------------------------------
# net
# --------------------------------------------------------------------------

def check_request_json_and_http_errors(http_api):
    http_api.add("GET", "/ok", {"value": 1})
    http_api.add("GET", "/denied", {"message": "bad key"}, status=401)
    http_api.add("GET", "/broken", b"not json")
    assert net.request_json("GET", http_api.base + "/ok", params={"q": "a b", "skip": None}) == {"value": 1}
    assert http_api.requests[-1].query == {"q": ["a b"]}
    assert http_api.requests[-1].headers["user-agent"].startswith("blender-mcp-bridge/")
    with pytest.raises(net.NetError, match="HTTP 401: bad key") as info:
        net.request_json("GET", http_api.base + "/denied")
    assert info.value.status == 401
    with pytest.raises(net.NetError, match="invalid JSON"):
        net.request_json("GET", http_api.base + "/broken")
    with pytest.raises(net.NetError, match="non-HTTP"):
        net.request_json("GET", "file:///etc/passwd")


def check_download_verifies_and_caches(http_api, tmp_path):
    entry = http_api.add_file("/f.bin", b"payload-bytes")
    target = tmp_path / "sub" / "f.bin"
    net.download(entry["url"], target, expected_md5=entry["md5"], expected_size=entry["size"])
    assert target.read_bytes() == b"payload-bytes"
    net.download(entry["url"], target, expected_md5=entry["md5"])
    assert http_api.hits("/f.bin") == 1  # second call reused the verified cached copy

    with pytest.raises(net.NetError, match="checksum"):
        net.download(entry["url"], tmp_path / "bad.bin", expected_md5="0" * 32)
    with pytest.raises(net.NetError, match="expected 99"):
        net.download(entry["url"], tmp_path / "short.bin", expected_size=99)
    with pytest.raises(net.NetError, match="exceeds"):
        net.download(entry["url"], tmp_path / "big.bin", max_bytes=4)
    assert not list(tmp_path.glob("*.partial")) and not (tmp_path / "bad.bin").exists()


@pytest.mark.parametrize("relpath", ["../x", "/abs/x", "a/../../x", "C:/x", "\\\\server\\share", ""])
def check_safe_join_rejects_escapes(tmp_path, relpath):
    with pytest.raises(net.NetError):
        net.safe_join(tmp_path, relpath)


def check_safe_join_keeps_nested_paths(tmp_path):
    assert net.safe_join(tmp_path, "textures/wood.jpg") == tmp_path / "textures" / "wood.jpg"
    assert net.safe_join(tmp_path, "textures\\wood.jpg") == tmp_path / "textures" / "wood.jpg"


def check_multipart_encoding_parses():
    from email.parser import BytesParser
    from email.policy import default

    body, content_type = net.encode_multipart([("prompt", "a chair"), ("tier", "Regular")],
                                              [("images", "ref.png", b"\x89PNGdata", "image/png")])
    message = BytesParser(policy=default).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
    assert parts["prompt"].get_content() == "a chair"
    assert parts["images"].get_filename() == "ref.png"
    assert parts["images"].get_content() == b"\x89PNGdata"


def check_cache_dir_per_platform(tmp_path):
    assert net.cache_dir(environ={"BLENDER_MCP_CACHE": str(tmp_path)}) == tmp_path
    assert net.cache_dir(platform="linux", environ={}, home=tmp_path) == tmp_path / ".cache" / "blender-mcp-bridge"
    assert net.cache_dir(platform="darwin", environ={}, home=tmp_path) == (
        tmp_path / "Library" / "Caches" / "blender-mcp-bridge")
    assert net.cache_dir(platform="win32", environ={"LOCALAPPDATA": str(tmp_path)}, home=tmp_path) == (
        tmp_path / "blender-mcp-bridge" / "cache")


# --------------------------------------------------------------------------
# transfer
# --------------------------------------------------------------------------

def check_stage_files_uses_shared_paths_when_blender_can_read_them(tmp_path):
    (tmp_path / "a.glb").write_bytes(b"x" * 10)
    connection = RecordingConnection(shared_files=True)
    staged = transfer.stage_files(connection.send_command, tmp_path, [tmp_path / "a.glb"])
    assert staged == {tmp_path / "a.glb": str(tmp_path / "a.glb")}
    assert connection.commands() == ["stat_file"]


def check_stage_files_uploads_in_chunks_for_remote_blender(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "CHUNK_BYTES", 7)
    payload = os.urandom(30)
    (tmp_path / "tex").mkdir()
    (tmp_path / "model.gltf").write_bytes(payload)
    (tmp_path / "tex" / "a.png").write_bytes(b"png")
    (tmp_path / "empty.bin").write_bytes(b"")
    connection = RecordingConnection(shared_files=False)
    files = [tmp_path / "model.gltf", tmp_path / "tex" / "a.png", tmp_path / "empty.bin"]
    staged = transfer.stage_files(connection.send_command, tmp_path, files)
    transfer_id = connection.calls[1][1]["transfer_id"]
    assert staged[files[0]] == f"/remote/{transfer_id}/model.gltf"
    assert staged[files[1]] == f"/remote/{transfer_id}/tex/a.png"
    assert connection.received[(transfer_id, "model.gltf")] == payload
    assert connection.received[(transfer_id, "empty.bin")] == b""
    assert connection.commands().count("receive_file") == 5 + 1 + 1


# --------------------------------------------------------------------------
# Poly Haven
# --------------------------------------------------------------------------

POLYHAVEN_ASSETS = {
    "kloppenheim": {"name": "Kloppenheim", "type": 0, "authors": {"Greg Zaal": "All"}, "download_count": 500,
                    "tags": ["sunset"], "categories": ["outdoor"]},
    "rock_wall": {"name": "Rock Wall", "type": 1, "authors": {"Rob Tuytel": "All"}, "download_count": 900,
                  "dimensions": [2000, 2000], "tags": ["stone", "rock"]},
    "potted_plant": {"name": "Potted Plant", "type": 2, "authors": {"Artist": "All"}, "download_count": 50},
}


@pytest.fixture
def polyhaven(http_api, tmp_path):
    api = http_api
    api.add("GET", "/assets", POLYHAVEN_ASSETS)
    api.add("GET", "/search", {"results": [{"slug": "potted_plant", "score": 0.4}, {"slug": "rock_wall", "score": 0.9},
                                           {"slug": "elsewhere"}]})
    api.add("GET", "/taxonomy/textures", {"categories": [
        {"path": "Brick", "children": [{"path": "Brick/Old", "children": [{"path": "Brick/Old/Red"}]}]},
        {"path": "Wood"}]})
    api.add("GET", "/info/kloppenheim", POLYHAVEN_ASSETS["kloppenheim"])
    api.add("GET", "/files/kloppenheim", {
        "hdri": {"1k": {"hdr": api.add_file("/HDRIs/1k/k.hdr", b"radiance-1k")},
                 "2k": {"hdr": api.add_file("/HDRIs/2k/k.hdr", b"radiance-2k")}},
        "tonemapped": api.add_file("/HDRIs/k.jpg", b"jpg"),
    })
    texture = {key: {"1k": {"jpg": api.add_file(f"/Textures/{key}.jpg", f"{key}-bytes".encode())}}
               for key in ("Diffuse", "nor_gl", "nor_dx", "Rough", "Displacement", "AO", "arm")}
    texture["blend"] = {"1k": {"blend": api.add_file("/Textures/x.blend", b"blend")}}
    api.add("GET", "/info/rock_wall", POLYHAVEN_ASSETS["rock_wall"])
    api.add("GET", "/files/rock_wall", texture)
    include = api.add_file("/Models/textures/pot_diff.jpg", b"pot-diffuse")
    api.add("GET", "/info/potted_plant", POLYHAVEN_ASSETS["potted_plant"])
    api.add("GET", "/files/potted_plant", {
        "blend": {"1k": {"blend": dict(api.add_file("/Models/potted_plant.blend", b"BLENDER-v293"),
                                       include={"textures/pot_diff_1k.jpg": include})}},
        "gltf": {"1k": {"gltf": dict(api.add_file("/Models/potted_plant.gltf", b'{"asset":{}}'),
                                     include={"textures/pot_diff_1k.jpg": include})}},
    })
    return PolyHaven(tmp_path / "cache", api=api.base)


def check_polyhaven_search_uses_semantic_ranking(polyhaven, http_api):
    result = polyhaven.search("plant pot", "models", limit=5)
    assert [a["id"] for a in result["assets"]] == ["potted_plant", "rock_wall"]
    assert result["assets"][0]["url"] == "https://polyhaven.com/a/potted_plant"
    assert result["assets"][0]["authors"] == ["Artist"]
    search = [r for r in http_api.requests if r.path == "/search"][0]
    assert search.query == {"q": ["plant pot"], "t": ["models"]}
    assets = [r for r in http_api.requests if r.path == "/assets"][0]
    assert assets.query == {"type": ["models"]}


def check_polyhaven_search_without_query_ranks_by_downloads(polyhaven):
    result = polyhaven.search(limit=2)
    assert [a["id"] for a in result["assets"]] == ["rock_wall", "kloppenheim"]
    assert result["total"] == 3
    assert result["assets"][0]["dimensions_mm"] == [2000, 2000]


def check_polyhaven_falls_back_to_keywords_when_search_is_down(polyhaven, http_api):
    http_api.add("GET", "/search", {"message": "embedding unavailable"}, status=503)
    result = polyhaven.search("rock")
    assert [a["id"] for a in result["assets"]] == ["rock_wall"]
    assert "keyword" in result["note"]


def check_polyhaven_reports_bad_category_and_bad_type(polyhaven, http_api):
    http_api.add("GET", "/assets", {"message": "bad category"}, status=400)
    with pytest.raises(AssetError, match="list_categories"):
        polyhaven.search(category="Nope")
    with pytest.raises(AssetError, match="hdris, textures, or models"):
        polyhaven.search(asset_type="sounds")


def check_polyhaven_categories_flatten_to_paths(polyhaven):
    assert polyhaven.categories("textures")["categories"] == ["Brick", "Brick/Old", "Wood"]


def check_polyhaven_fetch_hdri_downloads_once(polyhaven, http_api):
    fetched = polyhaven.fetch("kloppenheim")
    assert fetched.kind == "hdri" and fetched.name == "Kloppenheim"
    assert fetched.files[0].read_bytes() == b"radiance-1k"
    assert fetched.attribution["license"] == "CC0"
    assert fetched.custom_properties()["mcp_url"] == "https://polyhaven.com/a/kloppenheim"
    polyhaven.fetch("kloppenheim")
    assert http_api.hits("/HDRIs/1k/k.hdr") == 1
    with pytest.raises(AssetError, match="available resolutions: 1k, 2k"):
        polyhaven.fetch("kloppenheim", resolution="16k")


def check_polyhaven_fetch_texture_selects_pbr_maps(polyhaven):
    fetched = polyhaven.fetch("rock_wall", "textures")
    assert fetched.kind == "texture"
    assert set(fetched.maps) == {"base_color", "normal", "roughness", "displacement"}
    assert fetched.maps["normal"].read_bytes() == b"nor_gl-bytes"  # OpenGL normals, not DirectX
    assert fetched.attribution["dimensions_mm"] == [2000, 2000]


def check_polyhaven_texture_variants_use_first_color_map(http_api, tmp_path):
    http_api.add("GET", "/info/fabric", {"name": "Fabric", "type": 1})
    http_api.add("GET", "/files/fabric", {
        key: {"1k": {"jpg": http_api.add_file(f"/fab/{key}.jpg", key.encode())}} for key in ("col_2", "col_1", "Rough")})
    fetched = PolyHaven(tmp_path, api=http_api.base).fetch("fabric")
    assert fetched.maps["base_color"].read_bytes() == b"col_1"


def check_polyhaven_fetch_model_brings_includes_and_gltf_fallback(polyhaven):
    fetched = polyhaven.fetch("potted_plant")
    main, texture = fetched.files
    assert main.name == "potted_plant_1k.blend"
    assert texture == fetched.root / "textures" / "pot_diff_1k.jpg"
    assert texture.read_bytes() == b"pot-diffuse"
    assert fetched.blend_collections == ["potted_plant_LOD0", "potted_plant"]
    fallback = fetched.fallback()
    assert fallback.files[0].suffix == ".gltf"
    assert (fallback.root / "textures" / "pot_diff_1k.jpg").exists()


def check_polyhaven_refuses_hostile_include_paths(http_api, tmp_path):
    evil = http_api.add_file("/evil.png", b"evil")
    http_api.add("GET", "/info/trap", {"name": "Trap", "type": 2})
    http_api.add("GET", "/files/trap", {"blend": {"1k": {"blend": dict(
        http_api.add_file("/trap.blend", b"BLENDER"), include={"../../../escaped.png": evil})}}})
    with pytest.raises(AssetError, match="unsafe"):
        PolyHaven(tmp_path / "cache", api=http_api.base).fetch("trap")
    assert not list(tmp_path.rglob("escaped.png"))


def check_polyhaven_rejects_bad_ids_and_checksums(http_api, tmp_path):
    library = PolyHaven(tmp_path, api=http_api.base)
    with pytest.raises(AssetError, match="invalid Poly Haven asset id"):
        library.fetch("../etc")
    http_api.add("GET", "/info/bad", {"name": "Bad", "type": 0})
    entry = http_api.add_file("/bad.hdr", b"real")
    entry["md5"] = "0" * 32
    http_api.add("GET", "/files/bad", {"hdri": {"1k": {"hdr": entry}}})
    with pytest.raises(AssetError, match="checksum"):
        library.fetch("bad")


# --------------------------------------------------------------------------
# Sketchfab
# --------------------------------------------------------------------------

UID = "a" * 32


def _sketchfab_model():
    return {"uid": UID, "name": "Old Car", "user": {"displayName": "Jo", "username": "jo"},
            "license": {"label": "CC Attribution"}, "faceCount": 1200, "vertexCount": 900,
            "animationCount": 0, "isDownloadable": True, "viewerUrl": "https://sketchfab.com/3d-models/car",
            "thumbnails": {"images": [{"url": "s", "width": 100}, {"url": "m", "width": 256},
                                      {"url": "l", "width": 1024}]}}


def check_sketchfab_search_summaries(http_api, tmp_path):
    http_api.add("GET", "/v3/search", {"results": [_sketchfab_model()]})
    result = Sketchfab(None, tmp_path, api=http_api.base + "/v3").search("car", limit=50)
    asset = result["assets"][0]
    assert (asset["id"], asset["author"], asset["license"], asset["thumbnail_url"]) == (UID, "Jo", "CC Attribution", "m")
    assert "BLENDER_MCP_SKETCHFAB_API_KEY" in result["note"]
    query = http_api.requests[-1].query
    assert query["downloadable"] == ["true"] and query["count"] == ["24"] and query["type"] == ["models"]
    assert "authorization" not in http_api.requests[-1].headers


def check_sketchfab_download_needs_key_and_valid_uid(tmp_path):
    with pytest.raises(AssetError, match="BLENDER_MCP_SKETCHFAB_API_KEY"):
        Sketchfab(None, tmp_path).fetch(UID)
    with pytest.raises(AssetError, match="32 hex"):
        Sketchfab("key", tmp_path).fetch("../x")


def check_sketchfab_fetch_glb(http_api, tmp_path):
    http_api.add("GET", f"/v3/models/{UID}", _sketchfab_model())
    http_api.add("GET", f"/v3/models/{UID}/download", {"glb": {"url": http_api.base + "/dl/car.glb"}})
    http_api.add("GET", "/dl/car.glb", GLB)
    fetched = Sketchfab("secret", tmp_path, api=http_api.base + "/v3").fetch(UID)
    assert fetched.files[0].read_bytes() == GLB
    assert "Jo" in fetched.attribution["credit"] and "CC Attribution" in fetched.attribution["credit"]
    assert http_api.requests[0].headers["authorization"] == "Token secret"


def _zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def check_sketchfab_fetch_gltf_archive(http_api, tmp_path):
    http_api.add("GET", f"/v3/models/{UID}", _sketchfab_model())
    http_api.add("GET", f"/v3/models/{UID}/download", {"gltf": {"url": http_api.base + "/dl/car.zip"}})
    http_api.add("GET", "/dl/car.zip", _zip({"scene.gltf": '{"asset":{}}', "textures/body.png": "png",
                                             "scene.bin": "bin"}))
    fetched = Sketchfab("secret", tmp_path, api=http_api.base + "/v3").fetch(UID)
    assert fetched.files[0].name == "scene.gltf"
    assert (fetched.root / "textures" / "body.png").read_text() == "png"


def check_sketchfab_rejects_archive_traversal(http_api, tmp_path):
    http_api.add("GET", f"/v3/models/{UID}", _sketchfab_model())
    http_api.add("GET", f"/v3/models/{UID}/download", {"gltf": {"url": http_api.base + "/dl/evil.zip"}})
    http_api.add("GET", "/dl/evil.zip", _zip({"scene.gltf": "{}", "../../evil.txt": "x"}))
    with pytest.raises(AssetError, match="unsafe path"):
        Sketchfab("secret", tmp_path / "cache", api=http_api.base + "/v3").fetch(UID)
    assert not list(tmp_path.rglob("evil.txt"))


# --------------------------------------------------------------------------
# Poly Pizza
# --------------------------------------------------------------------------

def _pizza_model():
    return {"ID": "abc123", "Title": "Chair", "Creator": {"Username": "Quaternius"}, "Licence": "CC0 1.0",
            "Tri Count": 216, "Animated": False, "Category": "Furniture & Decor", "Tags": ["chair"],
            "Attribution": "Chair by Quaternius", "Thumbnail": "t.webp", "Download": None}


def check_polypizza_search_sends_numeric_filters(http_api, tmp_path):
    http_api.add("GET", "/v1.1/search/office%20chair", {"total": 1, "results": [_pizza_model()]})
    library = PolyPizza("pk", tmp_path, api=http_api.base + "/v1.1")
    result = library.search("office chair", category="Furniture & Decor", licence="CC0", animated=True, limit=99)
    assert result["assets"][0]["triangles"] == 216 and result["assets"][0]["author"] == "Quaternius"
    request = http_api.requests[-1]
    assert request.query == {"Limit": ["32"], "Category": ["4"], "License": ["1"], "Animated": ["1"]}
    assert request.headers["x-auth-token"] == "pk"


def check_polypizza_validates_input(tmp_path):
    library = PolyPizza("pk", tmp_path)
    with pytest.raises(AssetError, match="Furniture & Decor"):
        library.search("x", category="Spaceships")
    with pytest.raises(AssetError, match="CC0 or CC-BY"):
        library.search("x", licence="GPL")
    with pytest.raises(AssetError, match="keyword or a category"):
        library.search()
    with pytest.raises(AssetError, match="BLENDER_MCP_POLYPIZZA_API_KEY"):
        PolyPizza(None, tmp_path).search("chair")


def check_polypizza_fetch_checks_glb(http_api, tmp_path):
    model = dict(_pizza_model(), Download=http_api.base + "/static/chair.glb")
    http_api.add("GET", "/v1.1/model/abc123", model)
    http_api.add("GET", "/static/chair.glb", GLB)
    library = PolyPizza("pk", tmp_path, api=http_api.base + "/v1.1")
    fetched = library.fetch("abc123")
    assert fetched.files[0].read_bytes() == GLB
    assert fetched.attribution["credit"] == "Chair by Quaternius"
    http_api.add("GET", "/static/chair.glb", b"<html>challenge</html>")
    fetched.files[0].unlink()
    with pytest.raises(AssetError, match="not a GLB"):
        library.fetch("abc123")


# --------------------------------------------------------------------------
# Generation providers
# --------------------------------------------------------------------------

class FakeFetch:
    """Replays queued JSON responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def check_tripo_text_job_and_statuses():
    pytest.importorskip("mcp")
    from ai3dgenerator.core.errors import ProviderResponseError
    from blenderMcp.generation import TripoProvider

    fetch = FakeFetch([
        {"code": 0, "data": {"task_id": "t-1"}},
        {"code": 0, "data": {"status": "running", "progress": 40}},
        {"code": 0, "data": {"status": "success", "progress": 100,
                             "output": {"model": "https://cdn/m.glb", "pbr_model": "https://cdn/pbr.glb"}}},
        {"code": 0, "data": {"status": "banned"}},
        {"code": 2001, "message": "insufficient credit"},
    ])
    provider = TripoProvider("tk", api="https://tripo.test/v2/openapi", fetch=fetch)
    handle = provider.create_generation_job({"prompt": "a chest", "negative_prompt": "blurry"})
    method, url, kwargs = fetch.requests[0]
    assert (method, url, handle.job_id) == ("POST", "https://tripo.test/v2/openapi/task", "t-1")
    assert kwargs["json_body"] == {"type": "text_to_model", "prompt": "a chest", "negative_prompt": "blurry"}
    assert kwargs["headers"] == {"Authorization": "Bearer tk"}
    running = provider.get_job_status("t-1")
    assert (running.state, running.progress) == ("processing", 0.4)
    done = provider.get_job_status("t-1")
    assert (done.state, done.output_url) == ("completed", "https://cdn/pbr.glb")
    assert provider.get_job_status("t-1").state == "failed"
    with pytest.raises(ProviderResponseError, match="insufficient credit"):
        provider.create_generation_job({"prompt": "x"})


def check_tripo_image_job_uploads_first(tmp_path):
    pytest.importorskip("mcp")
    from blenderMcp.generation import TripoProvider

    image = tmp_path / "ref.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nrest")
    fetch = FakeFetch([{"code": 0, "data": {"image_token": "tok"}}, {"code": 0, "data": {"task_id": "t-2"}}])
    provider = TripoProvider("tk", api="https://tripo.test/v2/openapi", fetch=fetch)
    assert provider.create_image_job(str(image), {}).job_id == "t-2"
    upload = fetch.requests[0]
    assert upload[1].endswith("/upload") and upload[2]["content_type"].startswith("multipart/form-data")
    assert b'filename="ref.png"' in upload[2]["data"]
    assert fetch.requests[1][2]["json_body"] == {"type": "image_to_model", "file": {"type": "png", "file_token": "tok"}}


def check_rodin_job_lifecycle(tmp_path):
    pytest.importorskip("mcp")
    from blenderMcp.generation import RodinProvider

    fetch = FakeFetch([
        {"uuid": "u-1", "jobs": {"uuids": ["a", "b"], "subscription_key": "sub"}},
        {"jobs": [{"status": "Waiting"}, {"status": "Waiting"}]},
        {"jobs": [{"status": "Done"}, {"status": "Generating"}]},
        {"jobs": [{"status": "Done"}, {"status": "Done"}]},
        {"list": [{"name": "preview.webp", "url": "https://r/p.webp"}, {"name": "base.glb", "url": "https://r/m.glb"}]},
        {"jobs": [{"status": "Failed"}]},
    ])
    provider = RodinProvider("rk", api="https://rodin.test/api/v2", fetch=fetch)
    handle = provider.create_generation_job({"prompt": "a lamp"})
    submit = fetch.requests[0]
    assert submit[1] == "https://rodin.test/api/v2/rodin" and handle.job_id == "u-1"
    for field in (b'name="prompt"', b"a lamp", b'name="tier"', b'name="geometry_file_format"', b"glb"):
        assert field in submit[2]["data"]
    assert provider.get_job_status("u-1").state == "queued"
    generating = provider.get_job_status("u-1")
    assert (generating.state, generating.progress) == ("processing", 0.5)
    assert fetch.requests[1][2]["json_body"] == {"subscription_key": "sub"}
    done = provider.get_job_status("u-1")
    assert (done.state, done.output_url) == ("completed", "https://r/m.glb")
    assert fetch.requests[4][2]["json_body"] == {"task_uuid": "u-1"}
    assert provider.get_job_status("u-1").state == "failed"
    assert provider.get_job_status("unknown").state == "failed"


def check_generator_runs_mock_job_end_to_end(tmp_path):
    pytest.importorskip("mcp")
    from blenderMcp.generation import Generator

    generator = Generator(tmp_path)
    job = generator.start("mock", "a crate")
    job.thread.join(20)
    assert job.status()["state"] == "completed"
    assert generator.output_path(job).read_bytes()[:4] == b"glTF"
    failing = generator.start("mock", "mock-fail please")
    failing.thread.join(20)
    assert failing.status()["state"] == "failed"
    assert generator.get(job.job_id) is job


def check_generator_explains_missing_configuration(tmp_path):
    pytest.importorskip("mcp")
    from blenderMcp.generation import GenerationError, Generator

    generator = Generator(tmp_path)
    with pytest.raises(GenerationError, match="BLENDER_MCP_TRIPO_API_KEY"):
        generator.default_provider()
    with pytest.raises(GenerationError, match="BLENDER_MCP_HYPER3D_API_KEY"):
        generator.make_provider("hyper3d")
    with pytest.raises(GenerationError, match="unknown provider"):
        generator.make_provider("dalle")
    with pytest.raises(GenerationError, match="no generation job"):
        generator.get("nope")
    assert Generator(tmp_path, tripo_api_key="k", hyper3d_api_key="h").default_provider() == "tripo"


def check_custom_provider_config_file(tmp_path):
    pytest.importorskip("mcp")
    from blenderMcp.generation import GenerationError, Generator, load_custom_config

    path = tmp_path / "provider.json"
    path.write_text(json.dumps({"base_url": "https://gen.example", "job_id_path": "data.id", "colour": "red"}))
    with pytest.raises(GenerationError, match="unknown keys.*colour"):
        load_custom_config(str(path))
    path.write_text(json.dumps({"base_url": "https://gen.example", "job_id_path": "data.id"}))
    assert load_custom_config(str(path)).job_id_path == "data.id"
    provider = Generator(tmp_path, custom_config=str(path)).make_provider("custom_api")
    assert provider.identifier == "custom_api"


def check_tripo_generation_over_http(http_api, tmp_path):
    """Submit, poll, and download through ai3dgenerator's job and download managers."""
    pytest.importorskip("mcp")
    from blenderMcp.generation import Generator, TripoProvider

    http_api.add("POST", "/v2/openapi/task", {"code": 0, "data": {"task_id": "t-9"}})
    http_api.add("GET", "/v2/openapi/task/t-9", {"code": 0, "data": {
        "status": "success", "progress": 100, "output": {"model": http_api.base + "/out/t-9.glb"}}})
    http_api.add("GET", "/out/t-9.glb", GLB)
    generator = Generator(tmp_path, poll_interval=0.01,
                          factories={"tripo": lambda: TripoProvider("tk", api=http_api.base + "/v2/openapi")})
    job = generator.start("tripo", "a crate")
    job.thread.join(20)
    assert job.status()["state"] == "completed", job.status()
    assert generator.output_path(job).read_bytes() == GLB


# --------------------------------------------------------------------------
# Server tools for assets and generation
# --------------------------------------------------------------------------

class FakeLibrary:
    def __init__(self, fetched=None, search_result=None):
        self.fetched = fetched
        self.search_result = search_result or {"assets": []}
        self.calls = []

    def search(self, *args):
        self.calls.append(("search", args))
        return self.search_result

    def categories(self, *args):
        self.calls.append(("categories", args))
        return {"categories": ["Brick"]}

    def fetch(self, *args):
        self.calls.append(("fetch", args))
        return self.fetched


def _asset_server(connection, tmp_path, libraries, generator=None):
    pytest.importorskip("mcp")
    from blenderMcp.server import build_server

    factories = {name: (lambda lib=lib: lib) for name, lib in libraries.items()}
    return build_server(Settings(cache_dir=str(tmp_path / "cache")), connection, generator=generator,
                        asset_factories=factories)


def _fetched(tmp_path, kind, names, **extra):
    root = tmp_path / kind
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        files.append(path)
    return FetchedAsset("polyhaven", "thing", kind, "Thing", root, files,
                        attribution={"url": "https://polyhaven.com/a/thing", "license": "CC0"}, **extra)


def check_import_asset_hdri_sets_world(tmp_path):
    connection = RecordingConnection(results={"set_world_hdri": {"world": "Thing"}})
    library = FakeLibrary(_fetched(tmp_path, "hdri", ["thing.hdr"]))
    _, (result,) = run_tools(_asset_server(connection, tmp_path, {"polyhaven": library}), [
        ("import_asset", {"source": "polyhaven", "asset_id": "thing", "hdri_strength": 2, "hdri_rotation": 45})])
    assert not is_error(result), text_of(result)
    assert library.calls == [("fetch", ("thing", None, "1k", None))]
    command, params, _ = connection.calls[-1]
    assert command == "set_world_hdri"
    assert params["filepath"].endswith("thing.hdr") and params["strength"] == 2 and params["rotation_degrees"] == 45
    assert params["custom_properties"] == {"mcp_source": "polyhaven", "mcp_asset_id": "thing",
                                           "mcp_url": "https://polyhaven.com/a/thing", "mcp_license": "CC0"}
    assert json.loads(text_of(result))["attribution"]["license"] == "CC0"


def check_import_asset_texture_builds_material(tmp_path):
    connection = RecordingConnection()
    fetched = _fetched(tmp_path, "texture", ["diff.jpg", "nor.jpg"])
    fetched.maps = {"base_color": fetched.files[0], "normal": fetched.files[1]}
    _, (result,) = run_tools(_asset_server(connection, tmp_path, {"polyhaven": FakeLibrary(fetched)}), [
        ("import_asset", {"source": "polyhaven", "asset_id": "thing", "asset_type": "textures",
                          "apply_to": ["Wall"], "uv_scale": 3, "resolution": "2k"})])
    assert not is_error(result), text_of(result)
    command, params, _ = connection.calls[-1]
    assert command == "create_pbr_material"
    assert params["apply_to"] == ["Wall"] and params["uv_scale"] == 3 and params["name"] == "Thing"
    assert set(params["maps"]) == {"base_color", "normal"}


def check_import_asset_model_falls_back_to_gltf(tmp_path):
    connection = RecordingConnection(
        failures={"import_model": BlenderError("file is from a newer Blender", code="unreadable")},
        results={"import_model": {"imported_objects": ["Thing"]}})
    gltf = _fetched(tmp_path / "g", "model", ["thing.gltf", "textures/a.jpg"])
    fetched = _fetched(tmp_path, "model", ["thing.blend"], fallback=lambda: gltf,
                       blend_collections=["thing_LOD0", "thing"])
    _, (result,) = run_tools(_asset_server(connection, tmp_path, {"polyhaven": FakeLibrary(fetched)}), [
        ("import_asset", {"source": "polyhaven", "asset_id": "thing", "target_size": 1.5,
                          "location": [1, 2, 0], "collection": "Props"})])
    assert not is_error(result), text_of(result)
    imports = [params for command, params, _ in connection.calls if command == "import_model"]
    assert imports[0]["filepath"].endswith("thing.blend") and imports[0]["blend_collection"] == ["thing_LOD0", "thing"]
    assert imports[1]["filepath"].endswith("thing.gltf") and "blend_collection" not in imports[1]
    assert imports[1]["target_size"] == 1.5 and imports[1]["location"] == [1.0, 2.0, 0.0]
    assert "glTF" in json.loads(text_of(result))["blender"]["note"]


def check_import_asset_reports_library_errors(tmp_path):
    class Failing(FakeLibrary):
        def fetch(self, *args):
            raise AssetError("Poly Haven has no such asset")

    _, (result,) = run_tools(_asset_server(RecordingConnection(), tmp_path, {"polyhaven": Failing()}), [
        ("import_asset", {"source": "polyhaven", "asset_id": "ghost"})])
    assert is_error(result) and "no such asset" in text_of(result)


def check_search_assets_routes_and_validates(tmp_path):
    polyhaven = FakeLibrary(search_result={"assets": [{"id": "rock"}]})
    sketchfab = FakeLibrary()
    server = _asset_server(RecordingConnection(), tmp_path, {"polyhaven": polyhaven, "sketchfab": sketchfab})
    _, results = run_tools(server, [
        ("search_assets", {"source": "polyhaven", "query": "rock", "asset_type": "textures", "limit": 5}),
        ("search_assets", {"source": "polyhaven", "list_categories": True, "asset_type": "textures"}),
        ("search_assets", {"source": "polyhaven", "list_categories": True}),
        ("search_assets", {"source": "sketchfab"}),
    ])
    assert json.loads(text_of(results[0])) == {"assets": [{"id": "rock"}]}
    assert polyhaven.calls == [("search", ("rock", "textures", None, 5)), ("categories", ("textures",))]
    assert is_error(results[2]) and "needs asset_type" in text_of(results[2])
    assert is_error(results[3]) and "needs a query" in text_of(results[3])


def _generation_server(connection, tmp_path):
    from blenderMcp.generation import Generator

    return _asset_server(connection, tmp_path, {}, generator=Generator(tmp_path / "cache"))


def check_generate_3d_imports_mock_model(tmp_path):
    pytest.importorskip("mcp")
    connection = RecordingConnection(results={"import_model": {"imported_objects": ["Crate"]}})
    _, (result,) = run_tools(_generation_server(connection, tmp_path), [
        ("generate_3d", {"prompt": "a crate", "provider": "mock", "name": "Crate", "target_size": 0.5,
                         "wait_seconds": 30})])
    assert not is_error(result), text_of(result)
    status = json.loads(text_of(result))
    assert status["state"] == "completed" and status["imported"] == {"imported_objects": ["Crate"]}
    params = [p for c, p, _ in connection.calls if c == "import_model"][0]
    assert params["name"] == "Crate" and params["target_size"] == 0.5
    assert params["custom_properties"] == {"mcp_source": "mock", "mcp_prompt": "a crate"}
    assert Path(params["filepath"]).read_bytes()[:4] == b"glTF"


def check_slow_generation_returns_a_job_and_imports_once(tmp_path):
    pytest.importorskip("mcp")
    connection = RecordingConnection()

    async def script(client):
        first = json.loads(text_of(await client.call_tool(
            "generate_3d", {"prompt": "a crate", "provider": "mock", "wait_seconds": 0})))
        assert "get_generation_status" in first["next_step"]
        second = json.loads(text_of(await client.call_tool(
            "get_generation_status", {"job_id": first["job_id"], "wait_seconds": 30})))
        third = json.loads(text_of(await client.call_tool(
            "get_generation_status", {"job_id": first["job_id"], "wait_seconds": 0})))
        missing = await client.call_tool("get_generation_status", {"job_id": "nope"})
        return second, third, missing

    second, third, missing = run_client(_generation_server(connection, tmp_path), script)
    assert second["state"] == "completed" and second["imported"] == third["imported"]
    assert connection.commands().count("import_model") == 1
    assert is_error(missing) and "no generation job" in text_of(missing)


def check_generate_3d_input_and_configuration_errors(tmp_path):
    pytest.importorskip("mcp")
    _, (empty, unconfigured, failed) = run_tools(_generation_server(RecordingConnection(), tmp_path), [
        ("generate_3d", {}),
        ("generate_3d", {"prompt": "a chair"}),
        ("generate_3d", {"prompt": "mock-fail", "provider": "mock", "wait_seconds": 30}),
    ])
    assert is_error(empty) and "prompt" in text_of(empty)
    assert is_error(unconfigured) and "BLENDER_MCP_TRIPO_API_KEY" in text_of(unconfigured)
    assert is_error(failed) and "failed" in text_of(failed)


def check_guides_are_tools_resources_and_a_prompt(tmp_path):
    pytest.importorskip("mcp")
    from blenderMcp import guides

    async def script(client):
        guide = await client.call_tool("get_guide", {"topic": "materials"})
        resources = (await client.list_resources()).resources
        content = await client.read_resource("blender://guides/workflow")
        prompts = (await client.list_prompts()).prompts
        prompt = await client.get_prompt("build_scene", {"description": "a cozy cabin"})
        return guide, resources, content, prompts, prompt

    guide, resources, content, prompts, prompt = run_client(
        _asset_server(RecordingConnection(), tmp_path, {}), script)
    assert text_of(guide) == guides.load("materials")
    assert {str(r.uri) for r in resources} == {f"blender://guides/{t}" for t in guides.TOPICS}
    assert content.contents[0].text == guides.load("workflow")
    assert [p.name for p in prompts] == ["build_scene"]
    assert "a cozy cabin" in prompt.messages[0].content.text


def check_every_guide_loads():
    from blenderMcp import guides

    for topic in guides.TOPICS:
        assert guides.load(topic).startswith("# ")
    with pytest.raises(KeyError):
        guides.load("cooking")


# --------------------------------------------------------------------------
# update command
# --------------------------------------------------------------------------

def _install(folder: Path, version: str, extension: bool = False):
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text(f'BRIDGE_VERSION = "{version}"\n')
    if extension:
        (folder / "blender_manifest.toml").write_text(f'version = "{version}"\n')


def check_update_command_refreshes_old_copies_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(addonTools, "blender_config_root", lambda *a, **k: tmp_path / "none")
    monkeypatch.setenv("BLENDER_USER_SCRIPTS", str(tmp_path / "scripts"))
    monkeypatch.setenv("BLENDER_USER_EXTENSIONS", str(tmp_path / "extensions"))
    legacy = tmp_path / "scripts" / "addons" / "blender_mcp_bridge"
    extension = tmp_path / "extensions" / "user_default" / "blender_mcp_bridge"
    _install(legacy, "0.1.0")
    _install(extension, "0.1.0", extension=True)

    assert cli.main(["update", "--dry-run"]) == 0
    assert "would update 0.1.0" in capsys.readouterr().out
    assert addonTools.installed_version(legacy) == "0.1.0"

    assert cli.main(["update"]) == 0
    current = addonTools.ADDON_SOURCE / "__init__.py"
    assert (legacy / "__init__.py").read_text() == current.read_text()
    assert (legacy / "__init__.py.bak").read_text() == 'BRIDGE_VERSION = "0.1.0"\n'
    assert not (legacy / "blender_manifest.toml").exists()
    assert (extension / "blender_manifest.toml").read_text() == (
        addonTools.ADDON_SOURCE / "blender_manifest.toml").read_text()

    newer = tmp_path / "scripts" / "addons" / "blender_mcp_bridge"
    (newer / "__init__.py").write_text('BRIDGE_VERSION = "99.0.0"\n')
    capsys.readouterr()
    assert cli.main(["update"]) == 0
    assert "99.0.0 is up to date" in capsys.readouterr().out
    assert addonTools.installed_version(newer) == "99.0.0"


def check_headless_command_line(monkeypatch, tmp_path):
    import subprocess

    calls = []
    monkeypatch.setattr(subprocess, "call", lambda command, env=None: calls.append((command, env)) or 0)
    assert cli.main(["headless", "scene.blend", "--blender", "/opt/blender/blender", "--port", "9877",
                     "--token", "t0k", "--no-code", "--gpu"]) == 0
    command, env = calls[0]
    runner = Path(cli.__file__).resolve().parent / "runHeadless.py"
    assert command == ["/opt/blender/blender", "-b", "scene.blend", "--python", str(runner), "--",
                       "--port", "9877", "--token", "t0k", "--no-code"]
    assert env["BLENDER_MCP_GPU"] == "1"
    assert runner.is_file()


def check_headless_reports_missing_blender(capsys):
    assert cli.main(["headless", "--blender", str(Path("/nonexistent") / "blender")]) == 1
    assert "Blender executable not found" in capsys.readouterr().err
