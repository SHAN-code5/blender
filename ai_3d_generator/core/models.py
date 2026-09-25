"""Small, serializable domain models shared by services and providers."""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .errors import ValidationError


@dataclass
class GenerationRequest:
    prompt: str
    negative_prompt: str = ""
    quality: str = "standard"
    output_format: str = "glb"
    style: str = "realistic"
    polygon_target: int = 50000
    generate_materials: bool = True
    generate_textures: bool = True
    generation_mode: str = "text_to_3d"
    image_path: str = ""
    topology_preference: str = "balanced"
    texture_resolution: int = 2048
    auto_uv: bool = False
    prompt_enhanced: bool = False
    reference_image_path: str = ""
    original_prompt: str = ""

    def __post_init__(self) -> None:
        self.prompt = str(self.prompt)
        self.negative_prompt = str(self.negative_prompt)
        self.quality = str(self.quality)
        self.output_format = str(self.output_format).lower().lstrip(".")
        self.style = str(self.style)
        self.generation_mode = str(self.generation_mode)
        self.image_path = str(self.image_path)
        self.topology_preference = str(self.topology_preference)
        self.reference_image_path = str(self.reference_image_path)
        self.original_prompt = str(self.original_prompt)
        for field_name, lower, upper in (("polygon_target", 100, 10_000_000), ("texture_resolution", 256, 8192)):
            raw = getattr(self, field_name)
            if isinstance(raw, bool):
                raise ValidationError(f"{field_name} must be an integer.")
            try:
                numeric = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"{field_name} must be an integer.") from exc
            if not lower <= numeric <= upper:
                raise ValidationError(f"{field_name} is out of range.")
            setattr(self, field_name, numeric)
        for field_name in ("generate_materials", "generate_textures", "auto_uv", "prompt_enhanced"):
            if not isinstance(getattr(self, field_name), bool):
                raise ValidationError(f"{field_name} must be boolean.")
        if not self.reference_image_path and self.image_path:
            self.reference_image_path = self.image_path
        if self.generation_mode == "image_to_3d" and not self.reference_image_path:
            raise ValidationError("Image-to-3D requests require a reference image path.")
        if self.generation_mode not in {"text_to_3d", "image_to_3d"}:
            raise ValidationError("Unsupported generation mode.")
        if self.generation_mode == "text_to_3d" and not self.prompt.strip():
            raise ValidationError("Prompt cannot be empty.")

    def to_dict(self) -> Dict[str, Any]:
        """Return provider payload fields; never include credentials."""
        return {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "quality": self.quality,
            "output_format": self.output_format,
            "style": self.style,
            "polygon_target": self.polygon_target,
            "generate_materials": self.generate_materials,
            "generate_textures": self.generate_textures,
            "generation_mode": self.generation_mode,
            "image_path": self.image_path,
            "topology_preference": self.topology_preference,
            "texture_resolution": self.texture_resolution,
            "auto_uv": self.auto_uv,
            "prompt_enhanced": self.prompt_enhanced,
            "reference_image_path": self.reference_image_path,
            "original_prompt": self.original_prompt,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "GenerationRequest":
        if not isinstance(value, dict) or not isinstance(value.get("prompt"), str):
            raise ValueError("Generation request must contain a prompt.")
        allowed = cls.__dataclass_fields__
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass
class ProviderConfig:
    base_url: str = "http://127.0.0.1:8000"
    api_key: str = ""
    api_key_env: str = ""
    requires_api_key: bool = False
    model: str = "default"
    timeout: float = 60.0
    generate_path: str = "/generate"
    status_path: str = "/jobs/{job_id}"
    cancel_path: str = "/jobs/{job_id}/cancel"
    download_path: str = ""
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    headers: Dict[str, str] = field(default_factory=dict)
    request_payload: Dict[str, Any] = field(default_factory=dict)
    job_id_path: str = "id"
    status_path_value: str = "status"
    progress_path: str = "progress"
    output_url_path: str = "download_url"
    error_path: str = "error"
    max_asset_size_mb: int = 512
    poll_interval: float = 2.0
    job_timeout: float = 900.0
    download_host_allowlist: List[str] = field(default_factory=list)

    def public_dict(self) -> Dict[str, Any]:
        """Return non-secret configuration for diagnostics.

        Custom headers and request payload templates are user-entered and may
        contain credentials, so only their structure is exposed here.
        """
        result = asdict(self)
        result.pop("api_key", None)
        result["headers"] = {key: "<redacted>" for key in self.headers}
        result["request_payload"] = "<redacted>"
        return result


@dataclass
class JobHandle:
    job_id: str
    provider: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JobStatus:
    state: str
    progress: float = 0.0
    message: str = ""
    output_url: Optional[str] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HistoryEntry:
    job_id: str
    timestamp: str
    prompt: str
    negative_prompt: str
    provider: str
    model: str
    quality: str
    output_format: str
    file_path: str
    status: str
    error: str = ""
    generation_mode: str = "text_to_3d"
    image_path: str = ""
    style: str = "realistic"
    polygon_target: int = 50000
    topology_preference: str = "balanced"
    texture_resolution: int = 2048
    auto_uv: bool = False
    generate_materials: bool = True
    generate_textures: bool = True
    prompt_enhanced: bool = False
    original_prompt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "HistoryEntry":
        if not isinstance(value, dict):
            raise TypeError("History entry must be an object")
        defaults: Dict[str, Any] = {}
        for field_name, field_info in cls.__dataclass_fields__.items():
            if field_info.default is not MISSING:
                defaults[field_name] = field_info.default
            elif field_info.default_factory is not MISSING:
                defaults[field_name] = field_info.default_factory()
            else:
                defaults[field_name] = ""
        for key, default in defaults.items():
            field_value = value.get(key, default)
            if key == "error":
                field_value = str(field_value or "")
            if key in {"job_id", "timestamp", "prompt", "negative_prompt", "provider", "model", "quality", "output_format", "file_path", "status", "generation_mode", "image_path", "style", "topology_preference", "original_prompt"} and not isinstance(field_value, str):
                raise TypeError(f"{key} must be a string")
            if key in {"polygon_target", "texture_resolution"}:
                if not isinstance(field_value, int) or isinstance(field_value, bool):
                    raise TypeError(f"{key} must be an integer")
            elif key in {"auto_uv", "generate_materials", "generate_textures", "prompt_enhanced"}:
                if not isinstance(field_value, bool):
                    raise TypeError(f"{key} must be boolean")
            defaults[key] = field_value
        return cls(**defaults)


@dataclass
class ImportResult:
    file_path: str
    object_names: List[str] = field(default_factory=list)
    collection_name: str = ""
    root_object: str = ""
    imported_count: int = 0
    materials: List[str] = field(default_factory=list)
    warning: str = ""


@dataclass
class GenerationResult:
    job_id: str
    status: str
    file_path: str = ""
    message: str = ""
    error: str = ""
    import_result: Optional[ImportResult] = None
