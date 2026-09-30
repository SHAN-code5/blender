"""User-facing and technical exception types."""
from __future__ import annotations

from typing import Optional


class AI3DError(Exception):
    """Base exception with a safe message and optional technical detail."""

    def __init__(self, message: str, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def user_message(self) -> str:
        """Return a message safe to show in Blender's UI."""
        return self.message

    def technical_message(self) -> str:
        """Return message plus detail for the debug log only."""
        return f"{self.message}: {self.detail}" if self.detail else self.message


class ValidationError(AI3DError):
    """User input or a remote value failed validation."""


class ConfigurationError(ValidationError):
    """A provider or extension setting is invalid or missing."""


class AuthenticationError(AI3DError):
    """The provider rejected or could not authenticate the request."""


class NetworkError(AI3DError):
    """A network request failed or timed out."""


class ProviderResponseError(AI3DError):
    """A provider returned malformed or incomplete data."""


class DownloadError(AI3DError):
    """An asset could not be downloaded or validated."""


class ExportError(AI3DError):
    """A Blender export could not be completed or validated."""


class ImportError_(AI3DError):
    """Blender rejected an otherwise valid downloaded asset."""


class JobCancelled(AI3DError):
    """The user cancelled a running job."""


class JobTimeout(AI3DError):
    """A provider job exceeded the configured timeout."""
