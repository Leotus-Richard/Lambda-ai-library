"""Shared test harness.

Two seams are exercised here and nowhere else:

1. The MCP tool boundary, via an in-memory ``Client(server)``. Calls go through
   the real protocol layer, including argument validation.
2. The outbound HTTP boundary, via ``FakeAPI`` standing in for the Lambda Cloud
   API. That is an external trust boundary, not an internal collaborator.

Nothing below reaches into private helpers.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client

from lambda_mcp.client import LambdaClient
from lambda_mcp.server import build_server

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "spec" / "lambda-cloud-1.10.0.json"


@pytest.fixture(scope="session")
def spec() -> dict[str, Any]:
    """The vendored OpenAPI document, the contract this server implements."""
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeAPI:
    """Stand-in for the Lambda Cloud API.

    Records every outgoing request so tests can assert on the exact wire shape,
    and serves queued responses in order.
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self._responses: list[httpx2.Response] = []

    def queue(
        self,
        data: Any = None,
        *,
        status: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Queue one response. ``data`` is wrapped in the API's success envelope."""
        payload = body if body is not None else {"data": data}
        self._responses.append(
            httpx2.Response(status, json=payload, headers=headers or {})
        )

    def queue_error(
        self,
        code: str,
        *,
        status: int = 400,
        message: str = "something went wrong",
        suggestion: str | None = None,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Queue one response in the API's error envelope."""
        error: dict[str, Any] = {"code": code, "message": message}
        if suggestion is not None:
            error["suggestion"] = suggestion
        if request_id is not None:
            error["request_id"] = request_id
        self.queue(body={"error": error}, status=status, headers=headers)

    @property
    def last(self) -> httpx2.Request:
        assert self.requests, "no request was made"
        return self.requests[-1]

    def last_json(self) -> Any:
        return json.loads(self.last.content)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return httpx2.Response(200, json={"data": None})

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def sleeps() -> list[float]:
    """Records backoff delays so retry tests never actually wait."""
    return []


@pytest.fixture
def connect(api: FakeAPI, sleeps: list[float]):
    """Return a factory opening an in-memory MCP client against the fake API."""

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    @asynccontextmanager
    async def _connect(*, allow_write: bool = True, api_key: str = "test-key"):
        async with httpx2.AsyncClient(
            transport=api.transport(), base_url="https://cloud.lambda.ai"
        ) as http_client:
            lambda_client = LambdaClient(
                api_key=api_key,
                http_client=http_client,
                allow_write=allow_write,
                sleep=_record_sleep,
            )
            server = build_server(lambda_client, allow_write=allow_write)
            async with Client(server) as mcp_client:
                yield mcp_client

    return _connect
