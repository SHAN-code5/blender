"""Stable constants and identifiers for the extension."""
from __future__ import annotations

PACKAGE_NAME = "ai_3d_generator"
EXTENSION_NAME = "AI 3D Object Generator"
VERSION = "0.2.0"
GENERATED_COLLECTION_NAME = "AI3D_Generated"
HISTORY_FILENAME = "history.json"

SUPPORTED_FORMATS = ("glb", "gltf", "obj", "fbx", "stl")
QUALITY_LEVELS = ("draft", "standard", "high")
LOCATION_MODES = ("CURSOR", "ORIGIN", "COLLECTION")

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_TIMEOUT = "timeout"
STATUS_SUBMITTING = "submitting"
STATUS_CANCELLING = "cancelling"

TERMINAL_STATES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_TIMEOUT})

CONTENT_TYPE_BY_FORMAT = {
    "glb": "model/gltf-binary",
    "gltf": "model/gltf+json",
    "obj": "text/plain",
    "fbx": "application/octet-stream",
    "stl": "application/octet-stream",
}

MIME_FORMAT_BY_PREFIX = {
    "model/gltf-binary": "glb",
    "model/gltf+json": "gltf",
    "model/obj": "obj",
    "text/plain": "obj",
    "application/sla": "stl",
    "application/vnd.ms-pki.stl": "stl",
    "application/octet-stream": "",
}
