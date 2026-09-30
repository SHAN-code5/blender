"""Structured logging with secret redaction."""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path
from typing import Any, Optional, Union

from .constants import PACKAGE_NAME

LOGGER_NAME = PACKAGE_NAME
_SENSITIVE_KEYS = re.compile(r"(api[_-]?key|authorization|token|secret|password|credential)", re.I)
_URL_CREDENTIALS = re.compile(r"(https?://)([^/@\s]+):([^/@\s]+)@", re.I)


def redact(value: Any) -> Any:
    """Return a log-safe representation of a value."""
    if isinstance(value, dict):
        return {key: ("<redacted>" if _SENSITIVE_KEYS.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _URL_CREDENTIALS.sub(r"\1<redacted>:<redacted>@", value)
    return value


def get_logger(log_path: Optional[Union[Path, str]] = None, debug: bool = False) -> logging.Logger:
    """Return the package logger, optionally attaching a rotating file handler."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        marker = f"file::{path.resolve()}"
        if not any(getattr(handler, "_ai3d_marker", None) == marker for handler in logger.handlers):
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            handler._ai3d_marker = marker  # type: ignore[attr-defined]
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logger.addHandler(handler)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def log_context(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    """Log a message with redacted structured context."""
    logger.log(level, "%s %s", message, redact(context))
