"""HTTP client for the Lambda Cloud API.

Every tool goes through :meth:`LambdaClient.request`, which is the single place
that applies authentication, retries, unwraps the API's response envelope and
enforces the write gate. Keeping them here means a new tool cannot forget any
of them.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

import anyio
import httpx2

DEFAULT_BASE_URL = "https://cloud.lambda.ai"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_ATTEMPTS = 3

#: The API rate-limits to roughly one request per second, and upstream provider
#: failures surface as 5xx. Both are worth retrying; nothing else is.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SleepFn = Callable[[float], Awaitable[None]]


class WritesDisabledError(RuntimeError):
    """Raised when a mutating request is attempted with writes disabled."""


class LambdaAPIError(RuntimeError):
    """An error returned by the Lambda Cloud API.

    The rendered message leads with ``code`` deliberately: the spec warns that
    ``message`` and ``suggestion`` are subject to change, so ``code`` is the
    only value worth branching on.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        suggestion: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.request_id = request_id

        rendered = f"Lambda API error {status_code} [{code}]: {message}"
        if suggestion:
            rendered += f" Suggestion: {suggestion}"
        if request_id:
            rendered += f" (request_id: {request_id})"
        super().__init__(rendered)


class LambdaClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http_client: httpx2.AsyncClient | None = None,
        allow_write: bool = False,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sleep: SleepFn | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        self._api_key = api_key
        self._allow_write = allow_write
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep or anyio.sleep
        self._http = http_client or httpx2.AsyncClient(
            base_url=base_url, timeout=DEFAULT_TIMEOUT
        )

    @property
    def allow_write(self) -> bool:
        return self._allow_write

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        """Perform one API call and return the unwrapped ``data`` payload."""
        method = method.upper()
        if method not in _SAFE_METHODS and not self._allow_write:
            raise WritesDisabledError(
                "This server is running read-only. "
                "Set LAMBDA_MCP_ALLOW_WRITE=1 to enable mutating operations."
            )

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        cleaned = _clean_params(params)

        for attempt in range(self._max_attempts):
            response = await self._http.request(
                method, path, params=cleaned, json=json, headers=headers
            )
            is_last = attempt == self._max_attempts - 1
            if response.status_code in RETRY_STATUSES and not is_last:
                await self._sleep(_backoff_seconds(response, attempt))
                continue
            return _unwrap(response)

        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self._http.aclose()


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop optional query parameters the caller left unset."""
    if not params:
        return None
    cleaned = {k: v for k, v in params.items() if v is not None}
    return cleaned or None


def _backoff_seconds(response: httpx2.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    return float(2**attempt)


def _unwrap(response: httpx2.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        raise LambdaAPIError(
            response.status_code,
            str(error.get("code", "unknown")),
            str(error.get("message", "")),
            error.get("suggestion"),
            error.get("request_id"),
        )

    if response.status_code >= 400:
        raise LambdaAPIError(
            response.status_code,
            f"http/{response.status_code}",
            response.reason_phrase or "request failed",
        )

    if isinstance(payload, dict):
        return payload.get("data")
    return payload
