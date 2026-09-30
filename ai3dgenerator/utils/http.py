"""Standard-library HTTP client with bounded streaming and safe errors.

Requests are intentionally short-lived and run from Blender's timer callback
rather than from the UI draw handler. This keeps the main thread responsive
between bounded requests while avoiding a background-thread UI race.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Tuple

from ..core.errors import AuthenticationError, NetworkError, ProviderResponseError
from .validation import normalized_origin, parse_json_response, validate_http_url


@dataclass
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Strip redirected requests and enforce an exact-origin redirect policy."""

    def __init__(self, allowed_origins: Optional[Iterable[Any]] = None) -> None:
        origins = tuple(allowed_origins or ())
        self.allowed_origins = {self._normalize_origin(origin) for origin in origins if origin}
        self.allow_any_origin = not self.allowed_origins

    @staticmethod
    def _normalize_origin(value: Any) -> tuple[str, str, int]:
        if isinstance(value, (tuple, list)) and len(value) == 3:
            scheme, host, port = value
            return str(scheme).lower(), str(host).lower().rstrip("."), int(port)
        return normalized_origin(value, "Allowed redirect origin")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if str(req.get_method()).upper() not in {"GET", "HEAD"}:
            raise urllib.error.URLError("Redirects are not allowed for state-changing requests.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        try:
            target_origin = normalized_origin(redirected.full_url, "Redirect target")
            if not self.allow_any_origin and target_origin not in self.allowed_origins:
                raise urllib.error.URLError("Redirected request origin is not allowed.")
        except Exception as exc:
            raise urllib.error.URLError("Redirected request origin is not allowed.") from exc
        # Never replay a request body or caller-controlled headers through a
        # redirect. This is intentionally stricter than urllib's defaults.
        redirected.data = None
        redirected.headers = {}
        redirected.unredirected_hdrs = {}
        return redirected


class HttpClient:
    """Dependency-free JSON/bytes HTTP client with a bounded request size."""

    def __init__(self, timeout: float = 60.0, user_agent: str = "AI3DGenerator/0.2", allowed_redirect_hosts: Optional[Iterable[str]] = None, allowed_redirect_origins: Optional[Iterable[Any]] = None) -> None:
        timeout_value = float(timeout)
        if timeout_value != timeout_value or timeout_value in {float("inf"), float("-inf")} or not 0.1 <= timeout_value <= 600.0:
            raise ValueError("timeout must be between 0.1 and 600 seconds")
        self.timeout = timeout_value
        self.user_agent = user_agent
        self._allowed_redirect_origins = self._normalize_redirect_origins(allowed_redirect_hosts or (), allowed_redirect_origins or ())
        self._redirect_opener = urllib.request.build_opener(_NoAuthRedirect(self._allowed_redirect_origins))

    @staticmethod
    def _normalize_redirect_origins(hosts: Iterable[str], origins: Iterable[Any]) -> set[tuple[str, str, int]]:
        normalized = {_NoAuthRedirect._normalize_origin(origin) for origin in (origins or ()) if origin}
        for host in (hosts or ()):
            text = str(host).strip().lower().rstrip(".")
            if text:
                normalized.update({("http", text, 80), ("https", text, 443)})
        return normalized

    def set_allowed_redirect_hosts(self, hosts: Iterable[str]) -> None:
        """Compatibility alias; do not allow a legacy host policy to broaden an origin-bound client."""
        current = set(self._allowed_redirect_origins)
        exact = self._normalize_redirect_origins(hosts, ())
        if current and not exact.issubset(current):
            return
        self.set_allowed_redirect_origins(exact)

    def set_allowed_redirect_origins(self, origins: Iterable[Any]) -> None:
        """Set the exact-origin redirect policy for subsequent requests."""
        self._allowed_redirect_origins = self._normalize_redirect_origins((), origins)
        self._redirect_opener = urllib.request.build_opener(_NoAuthRedirect(self._allowed_redirect_origins))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Optional[Any] = None,
        timeout: Optional[float] = None,
        max_bytes: int = 8 * 1024 * 1024,
    ) -> HttpResponse:
        """Make a bounded HTTP request and return response bytes."""
        target = validate_http_url(url, "HTTP URL")
        request_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if headers:
            request_headers.update({str(key): str(value) for key, value in headers.items()})
        data = None
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(target, data=data, headers=request_headers, method=method.upper())
        timeout = self.timeout if timeout is None else float(timeout)
        if timeout != timeout or timeout in {float("inf"), float("-inf")} or not 0.1 <= timeout <= 600.0:
            raise ValueError("HTTP request timeout must be between 0.1 and 600 seconds")
        try:
            with self._redirect_opener.open(request, timeout=timeout) as response:
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ProviderResponseError("Provider response is larger than the configured limit.")
                return HttpResponse(int(response.status), dict(response.headers.items()), body)
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read(min(64 * 1024, max_bytes))
            except Exception:
                pass
            if exc.code in (401, 403):
                raise AuthenticationError("The provider rejected the API key or permissions.", detail=f"HTTP {exc.code}") from exc
            if exc.code == 429:
                raise NetworkError("The provider rate limit was reached. Try again later.", detail=f"HTTP {exc.code}") from exc
            if 500 <= exc.code <= 599:
                raise NetworkError("The provider is temporarily unavailable.", detail=f"HTTP {exc.code}") from exc
            raise ProviderResponseError("The provider rejected the request.", detail=f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            detail = str(reason)
            if "allowlist" in detail.lower() or "origin" in detail.lower():
                raise NetworkError("The provider request was blocked by the configured allowlist.", detail=detail) from exc
            raise NetworkError("Could not reach the provider. Check the URL and your internet connection.", detail=detail) from exc
        except TimeoutError as exc:
            raise NetworkError("The provider request timed out.", detail="timeout") from exc

    def request_json(self, method: str, url: str, **kwargs: Any) -> Tuple[Dict[str, Any], HttpResponse]:
        """Make a request and decode an object response."""
        response = self.request(method, url, **kwargs)
        return parse_json_response(response.body, response.headers.get("Content-Type", "")), response
