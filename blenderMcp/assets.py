"""Asset libraries: Poly Haven, Sketchfab, and Poly Pizza.

Searching and downloading happen in the MCP server process, so Blender's UI
never freezes on a slow transfer; Blender only imports finished files.
Every download is size-bounded and checksummed where the library publishes a
checksum, and server-supplied relative paths are confined to the cache folder.
"""

import re
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import net

SOURCES = ("polyhaven", "sketchfab", "polypizza")


class AssetError(RuntimeError):
    """A search or download failed in a way the model can act on."""


@dataclass
class FetchedAsset:
    """What was downloaded and how Blender should use it."""

    source: str
    asset_id: str
    kind: str  # "hdri", "texture", or "model"
    name: str
    root: Path
    files: List[Path]  # files[0] is the main file (model or HDRI); textures list every map
    maps: Dict[str, Path] = field(default_factory=dict)
    fallback: Optional[Callable[[], "FetchedAsset"]] = None  # e.g. glTF when a .blend is too new
    blend_collections: List[str] = field(default_factory=list)
    attribution: Dict[str, Any] = field(default_factory=dict)

    def custom_properties(self) -> Dict[str, Any]:
        props = {"mcp_source": self.source, "mcp_asset_id": self.asset_id}
        for key in ("url", "license", "author", "credit"):
            value = self.attribution.get(key)
            if value:
                props["mcp_" + key] = str(value)[:1000]
        return props


def _net_error(source: str, exc: net.NetError) -> AssetError:
    if exc.status in (401, 403):
        return AssetError(f"{source} rejected the API key ({exc}). Check the key and its permissions.")
    if exc.status == 404:
        return AssetError(f"{source} has no such asset ({exc}).")
    if exc.status == 429:
        wait = f" Retry in {exc.retry_after}s." if exc.retry_after else ""
        return AssetError(f"{source} is rate limiting requests.{wait}")
    return AssetError(f"{source} request failed: {exc}")


# --------------------------------------------------------------------------
# Poly Haven (CC0, no API key)
# --------------------------------------------------------------------------

POLYHAVEN_API = "https://api.polyhaven.com"
POLYHAVEN_SITE = "https://polyhaven.com"
POLYHAVEN_TYPES = {0: "hdris", 1: "textures", 2: "models"}
POLYHAVEN_FORMATS = {"hdris": ("hdr", "exr"), "textures": ("jpg", "png", "exr")}
POLYHAVEN_MAPS = {
    "Diffuse": "base_color",
    "Rough": "roughness",
    "Metal": "metallic",
    "Displacement": "displacement",
    "nor_gl": "normal",
    "nor_dx": "normal",
}
_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _resolution_key(resolution: str) -> int:
    try:
        return int(str(resolution).rstrip("k"))
    except ValueError:
        return -1


class PolyHaven:
    def __init__(self, cache_root: Path, api: str = POLYHAVEN_API, fetch=net.request_json,
                 download=net.download) -> None:
        self.cache_root = Path(cache_root) / "polyhaven"
        self.api = api.rstrip("/")
        self._fetch = fetch
        self._download = download

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        return self._fetch("GET", f"{self.api}/{path}", params=params)

    @staticmethod
    def _check_type(asset_type: Optional[str], allow_all: bool = True) -> Optional[str]:
        if asset_type in (None, "", "all") and allow_all:
            return None
        if asset_type not in POLYHAVEN_TYPES.values():
            raise AssetError("Poly Haven asset_type must be hdris, textures, or models")
        return asset_type

    def categories(self, asset_type: str, depth: int = 2) -> Dict[str, Any]:
        asset_type = self._check_type(asset_type, allow_all=False)
        try:
            payload = self._get(f"taxonomy/{asset_type}")
        except net.NetError as exc:
            raise _net_error("Poly Haven", exc) from exc
        paths: List[str] = []

        def walk(nodes, level):
            for node in nodes or []:
                if node.get("path"):
                    paths.append(node["path"])
                if level < depth:
                    walk(node.get("children"), level + 1)

        walk(payload.get("categories"), 1)
        return {"source": "polyhaven", "asset_type": asset_type, "categories": paths}

    def search(self, query: Optional[str] = None, asset_type: Optional[str] = None,
               category: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        asset_type = self._check_type(asset_type)
        params = {"type": asset_type, "category": category}
        try:
            assets = self._get("assets", params)
        except net.NetError as exc:
            if exc.status == 400:
                raise AssetError("Poly Haven did not recognise that category. Call search_assets with "
                                 "list_categories=true for the valid paths.") from exc
            raise _net_error("Poly Haven", exc) from exc
        if not isinstance(assets, dict):
            raise AssetError("Poly Haven returned an unexpected asset list")
        query = (query or "").strip().lower()
        note = None
        if query:
            try:
                payload = self._get("search", {"q": query, "t": asset_type})
                ranked = [r.get("slug") for r in payload.get("results") or [] if isinstance(r, dict)]
            except net.NetError as exc:
                if exc.status != 503:
                    raise _net_error("Poly Haven", exc) from exc
                # Documented fallback when the semantic search is unavailable.
                ranked = self._keyword_rank(query, assets)
                note = "Poly Haven's semantic search was unavailable; these are keyword matches."
            ordered = [slug for slug in ranked if slug in assets]
        else:
            ordered = sorted(assets, key=lambda s: assets[s].get("download_count") or 0, reverse=True)
        limit = max(1, min(int(limit or 20), 50))
        return {
            "source": "polyhaven",
            "total": len(ordered),
            "assets": [self._summary(slug, assets[slug]) for slug in ordered[:limit]],
            "note": note,
            "license": "CC0: free for any use, no attribution required",
        }

    @staticmethod
    def _keyword_rank(query: str, assets: Dict[str, Any]) -> List[str]:
        terms = query.split()
        scored = []
        for slug, record in assets.items():
            text = " ".join([slug.replace("_", " "), str(record.get("name") or ""),
                             " ".join(record.get("tags") or []), " ".join(record.get("categories") or [])]).lower()
            hits = sum(1 for term in terms if term in text)
            if hits:
                scored.append((hits, record.get("download_count") or 0, slug))
        return [slug for _, _, slug in sorted(scored, reverse=True)]

    @staticmethod
    def _summary(slug: str, record: Dict[str, Any]) -> Dict[str, Any]:
        authors = record.get("authors") or {}
        summary = {
            "id": slug,
            "name": record.get("name") or slug,
            "type": POLYHAVEN_TYPES.get(record.get("type"), "unknown"),
            "authors": sorted(authors) if isinstance(authors, dict) else authors,
            "downloads": record.get("download_count"),
            "url": f"{POLYHAVEN_SITE}/a/{slug}",
        }
        for key in ("category", "tags", "max_resolution", "thumbnail_url"):
            if record.get(key):
                summary[key] = record[key]
        if record.get("dimensions"):
            summary["dimensions_mm"] = record["dimensions"]
        return summary

    def fetch(self, asset_id: str, asset_type: Optional[str] = None, resolution: str = "1k",
              file_format: Optional[str] = None) -> FetchedAsset:
        if not isinstance(asset_id, str) or not _SLUG.match(asset_id):
            raise AssetError(f"invalid Poly Haven asset id {asset_id!r}")
        resolution = (resolution or "1k").lower()
        try:
            info = self._get(f"info/{asset_id}")
            files = self._get(f"files/{asset_id}")
        except net.NetError as exc:
            raise _net_error("Poly Haven", exc) from exc
        asset_type = self._check_type(asset_type) or POLYHAVEN_TYPES.get(info.get("type"))
        if asset_type is None:
            raise AssetError(f"cannot tell what kind of asset {asset_id} is; pass asset_type")
        authors = info.get("authors") or {}
        attribution = {
            "url": f"{POLYHAVEN_SITE}/a/{asset_id}",
            "license": "CC0",
            "author": ", ".join(sorted(authors)) if isinstance(authors, dict) else str(authors),
            "credit": f"{info.get('name') or asset_id} from Poly Haven (CC0)",
        }
        folder = self.cache_root / asset_id / resolution
        name = info.get("name") or asset_id

        if asset_type == "hdris":
            fmt = (file_format or "hdr").lower()
            entry = self._pick(files.get("hdri"), resolution, fmt, asset_id)
            path = self._get_file(entry, folder / f"{asset_id}_{resolution}.{fmt}")
            return FetchedAsset("polyhaven", asset_id, "hdri", name, folder, [path], attribution=attribution)

        if asset_type == "textures":
            fmt = (file_format or "jpg").lower()
            selected = self._texture_maps(files, resolution, fmt)
            if not selected:
                raise AssetError(f"{asset_id} has no {fmt} maps at {resolution}; "
                                 + self._available(files, ("jpg", "png", "exr")))
            maps = {}
            for key, role in selected.items():
                entry = files[key][resolution][fmt]
                maps[role] = self._get_file(entry, folder / f"{asset_id}_{key}_{resolution}.{fmt}")
            result = FetchedAsset("polyhaven", asset_id, "texture", name, folder, list(maps.values()),
                                  maps=maps, attribution=attribution)
            if info.get("dimensions"):
                result.attribution["dimensions_mm"] = info["dimensions"]
            return result

        # Models: the .blend the artist authored (with its textures), glTF as a fallback.
        entry = self._pick(files.get("blend"), resolution, "blend", asset_id)
        model_files = self._get_bundle(entry, folder / "blend", f"{asset_id}_{resolution}.blend")

        def gltf_fallback() -> FetchedAsset:
            gltf_entry = self._pick(files.get("gltf"), resolution, "gltf", asset_id)
            bundle = self._get_bundle(gltf_entry, folder / "gltf", f"{asset_id}_{resolution}.gltf")
            return FetchedAsset("polyhaven", asset_id, "model", name, folder / "gltf", bundle,
                                attribution=attribution)

        return FetchedAsset("polyhaven", asset_id, "model", name, folder / "blend", model_files,
                            fallback=gltf_fallback if files.get("gltf") else None,
                            blend_collections=[f"{asset_id}_LOD0", asset_id], attribution=attribution)

    def _pick(self, by_resolution: Any, resolution: str, fmt: str, asset_id: str) -> Dict[str, Any]:
        entry = (by_resolution or {}).get(resolution, {}).get(fmt) if isinstance(by_resolution, dict) else None
        if not isinstance(entry, dict) or not entry.get("url"):
            available = sorted((by_resolution or {}).keys(), key=_resolution_key) if isinstance(by_resolution, dict) else []
            raise AssetError(f"{asset_id} has no {fmt} file at {resolution}; available resolutions: "
                             f"{', '.join(available) or 'none'}")
        return entry

    @staticmethod
    def _available(files: Dict[str, Any], formats) -> str:
        resolutions = set()
        for key in POLYHAVEN_MAPS:
            for res, by_format in (files.get(key) or {}).items():
                if isinstance(by_format, dict) and any(f in by_format for f in formats):
                    resolutions.add(res)
        return "available resolutions: " + (", ".join(sorted(resolutions, key=_resolution_key)) or "none")

    @staticmethod
    def _texture_maps(files: Dict[str, Any], resolution: str, fmt: str) -> Dict[str, str]:
        selected = {}
        for key, role in POLYHAVEN_MAPS.items():
            by_resolution = files.get(key)
            if isinstance(by_resolution, dict) and fmt in (by_resolution.get(resolution) or {}):
                if role == "normal" and "normal" in selected.values():
                    continue  # nor_gl (OpenGL, what Blender expects) is listed before nor_dx
                selected[key] = role
        if "base_color" not in selected.values():
            # Multi-variant textures name their albedo col_1, col_2, ... instead of Diffuse.
            for key in sorted(files):
                if key.lower().startswith(("col", "diff")):
                    by_resolution = files.get(key)
                    if isinstance(by_resolution, dict) and fmt in (by_resolution.get(resolution) or {}):
                        selected[key] = "base_color"
                        break
        return selected

    def _get_file(self, entry: Dict[str, Any], destination: Path) -> Path:
        try:
            return self._download(entry["url"], destination, expected_md5=entry.get("md5"),
                                  expected_size=entry.get("size"))
        except net.NetError as exc:
            raise _net_error("Poly Haven", exc) from exc

    def _get_bundle(self, entry: Dict[str, Any], folder: Path, main_name: str) -> List[Path]:
        """Download a main file plus its ``include`` files (textures) at their relative paths."""
        paths = [self._get_file(entry, folder / main_name)]
        for relpath, include in (entry.get("include") or {}).items():
            try:
                target = net.safe_join(folder, relpath)
            except net.NetError as exc:
                raise AssetError(f"Poly Haven listed an unsafe file path: {exc}") from exc
            paths.append(self._get_file(include, target))
        return paths


# --------------------------------------------------------------------------
# Sketchfab (search is public; downloads need an API token)
# --------------------------------------------------------------------------

SKETCHFAB_API = "https://api.sketchfab.com/v3"
MAX_ARCHIVE_BYTES = 2 * 1024 ** 3
MAX_ARCHIVE_FILES = 5000


def safe_extract(archive_path: Path, folder: Path) -> List[Path]:
    """Extract a zip, rejecting path traversal and archive bombs."""
    folder.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(archive_path) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > MAX_ARCHIVE_FILES:
            raise AssetError("archive has too many files")
        if sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES:
            raise AssetError("archive expands beyond the size limit")
        for member in members:
            try:
                target = net.safe_join(folder, member.filename)
            except net.NetError as exc:
                raise AssetError(f"archive contains an unsafe path: {exc}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, "wb") as handle:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            extracted.append(target)
    return extracted


class Sketchfab:
    def __init__(self, api_key: Optional[str], cache_root: Path, api: str = SKETCHFAB_API,
                 fetch=net.request_json, download=net.download) -> None:
        self.api_key = api_key
        self.cache_root = Path(cache_root) / "sketchfab"
        self.api = api.rstrip("/")
        self._fetch = fetch
        self._download = download

    def _headers(self, required: bool = False) -> Dict[str, str]:
        if not self.api_key:
            if required:
                raise AssetError("Sketchfab downloads need an API token: set BLENDER_MCP_SKETCHFAB_API_KEY "
                                 "(sketchfab.com > Settings > Password & API).")
            return {}
        return {"Authorization": f"Token {self.api_key}"}

    def search(self, query: str, limit: int = 20, category: Optional[str] = None,
               downloadable: bool = True) -> Dict[str, Any]:
        params = {"type": "models", "q": query, "count": max(1, min(int(limit or 20), 24)),
                  "categories": category}
        if downloadable:
            params["downloadable"] = "true"
        try:
            payload = self._fetch("GET", f"{self.api}/search", params=params, headers=self._headers())
        except net.NetError as exc:
            raise _net_error("Sketchfab", exc) from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise AssetError("Sketchfab returned an unexpected search response")
        return {"source": "sketchfab", "assets": [self._summary(m) for m in results],
                "note": None if self.api_key else "Set BLENDER_MCP_SKETCHFAB_API_KEY to import these models."}

    @staticmethod
    def _summary(model: Dict[str, Any]) -> Dict[str, Any]:
        user = model.get("user") or {}
        license_info = model.get("license") or {}
        images = ((model.get("thumbnails") or {}).get("images") or [])
        thumbnail = min(images, key=lambda i: abs((i.get("width") or 0) - 256), default={}).get("url")
        return {
            "id": model.get("uid"),
            "name": model.get("name"),
            "author": user.get("displayName") or user.get("username"),
            "license": license_info.get("label") if isinstance(license_info, dict) else license_info,
            "faces": model.get("faceCount"),
            "vertices": model.get("vertexCount"),
            "animated": bool(model.get("animationCount")),
            "downloadable": model.get("isDownloadable"),
            "url": model.get("viewerUrl"),
            "thumbnail_url": thumbnail,
        }

    def fetch(self, uid: str) -> FetchedAsset:
        if not isinstance(uid, str) or not re.match(r"^[0-9a-fA-F]{32}$", uid):
            raise AssetError(f"invalid Sketchfab model id {uid!r} (expected 32 hex characters)")
        headers = self._headers(required=True)
        try:
            info = self._fetch("GET", f"{self.api}/models/{uid}", headers=headers)
            links = self._fetch("GET", f"{self.api}/models/{uid}/download", headers=headers)
        except net.NetError as exc:
            raise _net_error("Sketchfab", exc) from exc
        summary = self._summary(info if isinstance(info, dict) else {})
        attribution = {
            "url": summary.get("url") or f"https://sketchfab.com/3d-models/{uid}",
            "license": summary.get("license"),
            "author": summary.get("author"),
            "credit": f"\"{summary.get('name') or uid}\" by {summary.get('author') or 'unknown'} "
                      f"on Sketchfab, licensed {summary.get('license') or 'see the model page'}",
        }
        folder = self.cache_root / uid
        try:
            if isinstance(links.get("glb"), dict) and links["glb"].get("url"):
                path = self._download(links["glb"]["url"], folder / f"{uid}.glb")
                return FetchedAsset("sketchfab", uid, "model", summary.get("name") or uid, folder, [path],
                                    attribution=attribution)
            archive_url = (links.get("gltf") or {}).get("url")
            if not archive_url:
                raise AssetError(f"Sketchfab offers no glTF download for {uid}; it may not be downloadable")
            archive = self._download(archive_url, folder / f"{uid}.zip")
        except net.NetError as exc:
            raise _net_error("Sketchfab", exc) from exc
        files = safe_extract(archive, folder / "gltf")
        scenes = sorted((f for f in files if f.suffix.lower() in (".gltf", ".glb")), key=lambda p: len(p.parts))
        if not scenes:
            raise AssetError("the Sketchfab archive contains no .gltf or .glb file")
        main = scenes[0]
        return FetchedAsset("sketchfab", uid, "model", summary.get("name") or uid, folder / "gltf",
                            [main] + [f for f in files if f != main], attribution=attribution)


# --------------------------------------------------------------------------
# Poly Pizza (API key; about 10k low-poly models, CC0 and CC-BY)
# --------------------------------------------------------------------------

POLYPIZZA_API = "https://api.poly.pizza/v1.1"
POLYPIZZA_CATEGORIES = {
    "food & drink": 0, "clutter": 1, "weapons": 2, "transport": 3, "furniture & decor": 4, "objects": 5,
    "nature": 6, "animals": 7, "buildings": 8, "people & characters": 9, "scenes & levels": 10, "other": 11,
}
POLYPIZZA_LICENCES = {"cc-by": 0, "ccby": 0, "by": 0, "cc0": 1, "public domain": 1}


class PolyPizza:
    def __init__(self, api_key: Optional[str], cache_root: Path, api: str = POLYPIZZA_API,
                 fetch=net.request_json, download=net.download) -> None:
        self.api_key = api_key
        self.cache_root = Path(cache_root) / "polypizza"
        self.api = api.rstrip("/")
        self._fetch = fetch
        self._download = download

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise AssetError("Poly Pizza needs a free API key: set BLENDER_MCP_POLYPIZZA_API_KEY "
                             "(poly.pizza/settings/api).")
        return {"x-auth-token": self.api_key}

    def search(self, query: Optional[str] = None, category: Optional[str] = None, licence: Optional[str] = None,
               animated: bool = False, limit: int = 20, page: Optional[int] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"Limit": max(1, min(int(limit or 20), 32)), "Page": page}
        if category:
            key = category.strip().lower()
            if key not in POLYPIZZA_CATEGORIES:
                raise AssetError("Poly Pizza category must be one of: "
                                 + ", ".join(k.title() for k in POLYPIZZA_CATEGORIES))
            params["Category"] = POLYPIZZA_CATEGORIES[key]
        if licence:
            key = licence.strip().lower().replace(" ", "")
            key = {"cc-by4.0": "cc-by", "cc-by3.0": "cc-by", "cc01.0": "cc0"}.get(key, key)
            if key not in POLYPIZZA_LICENCES:
                raise AssetError("Poly Pizza licence must be CC0 or CC-BY")
            params["License"] = POLYPIZZA_LICENCES[key]
        if animated:
            params["Animated"] = 1
        keyword = (query or "").strip()
        if not keyword and len(params) <= 2:
            raise AssetError("Poly Pizza needs a search keyword or a category/licence/animated filter")
        url = f"{self.api}/search/{urllib.parse.quote(keyword, safe='')}" if keyword else f"{self.api}/search"
        try:
            payload = self._fetch("GET", url, params=params, headers=self._headers())
        except net.NetError as exc:
            raise _net_error("Poly Pizza", exc) from exc
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise AssetError("Poly Pizza returned an unexpected search response")
        return {"source": "polypizza", "total": payload.get("total"),
                "assets": [self._summary(m) for m in results]}

    @staticmethod
    def _summary(model: Dict[str, Any]) -> Dict[str, Any]:
        creator = model.get("Creator") or {}
        return {
            "id": model.get("ID"),
            "name": model.get("Title"),
            "author": creator.get("Username") if isinstance(creator, dict) else None,
            "license": model.get("Licence"),
            "triangles": model.get("Tri Count"),
            "animated": bool(model.get("Animated")),
            "category": model.get("Category"),
            "tags": model.get("Tags") or [],
            "attribution": model.get("Attribution"),
            "thumbnail_url": model.get("Thumbnail"),
        }

    def fetch(self, model_id: str) -> FetchedAsset:
        if not isinstance(model_id, str) or not re.match(r"^[A-Za-z0-9_-]{1,64}$", model_id):
            raise AssetError(f"invalid Poly Pizza model id {model_id!r}")
        try:
            model = self._fetch("GET", f"{self.api}/model/{urllib.parse.quote(model_id, safe='')}",
                                headers=self._headers())
        except net.NetError as exc:
            raise _net_error("Poly Pizza", exc) from exc
        summary = self._summary(model if isinstance(model, dict) else {})
        url = (model or {}).get("Download")
        if not url:
            raise AssetError(f"Poly Pizza model {model_id} has no downloadable GLB")
        folder = self.cache_root / model_id
        try:
            path = self._download(url, folder / f"{model_id}.glb")
        except net.NetError as exc:
            if exc.status in (403, 503):
                raise AssetError("static.poly.pizza blocked the download (it challenges datacenter and VPN "
                                 "addresses). Retry from a home connection.") from exc
            raise _net_error("Poly Pizza", exc) from exc
        with open(path, "rb") as handle:
            if handle.read(4) != b"glTF":
                path.unlink(missing_ok=True)
                raise AssetError("Poly Pizza returned a file that is not a GLB")
        licence = summary.get("license") or ""
        attribution = {
            "url": f"https://poly.pizza/m/{model_id}",
            "license": licence,
            "author": summary.get("author"),
            "credit": summary.get("attribution") or f"{summary.get('name')} by {summary.get('author')} ({licence})",
        }
        return FetchedAsset("polypizza", model_id, "model", summary.get("name") or model_id, folder, [path],
                            attribution=attribution)
