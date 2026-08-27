"""Client behaviour: error mapping, retries and the write gate.

These are exercised through the MCP tool boundary wherever possible, using
``list_regions`` as a neutral vehicle, so the assertions describe what a real
client observes rather than how the code is arranged internally.
"""

from __future__ import annotations

import httpx2
import pytest

from lambda_mcp.client import LambdaClient, WritesDisabledError

pytestmark = pytest.mark.anyio


def _error_text(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "text", None)
    )


async def test_api_error_reports_code_suggestion_and_request_id(api, connect):
    api.queue_error(
        "global/object-does-not-exist",
        status=404,
        message="Region not found",
        suggestion="Check the region name",
        request_id="req-123",
    )

    async with connect() as client:
        result = await client.call_tool("list_regions", {})

    assert result.is_error
    text = _error_text(result)
    assert "global/object-does-not-exist" in text
    assert "Region not found" in text
    assert "Check the region name" in text
    assert "req-123" in text


async def test_api_error_never_leaks_the_api_key(api, connect):
    api.queue_error("global/invalid-api-key", status=401, message="Unauthorized")

    async with connect(api_key="super-secret-key") as client:
        result = await client.call_tool("list_regions", {})

    assert result.is_error
    assert "super-secret-key" not in _error_text(result)


async def test_retries_after_429_and_then_succeeds(api, connect, sleeps):
    api.queue(body={"error": {"code": "rate-limited", "message": "slow down"}}, status=429)
    api.queue(data=[{"name": "us-west-1", "description": "California, USA"}])

    async with connect() as client:
        result = await client.call_tool("list_regions", {})

    assert not result.is_error
    assert len(api.requests) == 2
    assert result.structured_content["result"][0]["name"] == "us-west-1"


async def test_honors_retry_after_header(api, connect, sleeps):
    api.queue(
        body={"error": {"code": "rate-limited", "message": "slow down"}},
        status=429,
        headers={"Retry-After": "7"},
    )
    api.queue(data=[])

    async with connect() as client:
        await client.call_tool("list_regions", {})

    assert sleeps == [7.0]


async def test_retries_on_server_error(api, connect):
    api.queue(body={"error": {"code": "provider/internal-unavailable", "message": "nope"}}, status=503)
    api.queue(data=[])

    async with connect() as client:
        result = await client.call_tool("list_regions", {})

    assert not result.is_error
    assert len(api.requests) == 2


async def test_gives_up_and_reports_after_exhausting_retries(api, connect):
    for _ in range(5):
        api.queue(
            body={"error": {"code": "rate-limited", "message": "slow down"}}, status=429
        )

    async with connect() as client:
        result = await client.call_tool("list_regions", {})

    assert result.is_error
    assert "rate-limited" in _error_text(result)
    assert len(api.requests) == 3


async def test_client_refuses_mutating_requests_when_writes_disabled():
    async def _no_sleep(_seconds: float) -> None:
        return None

    def _handler(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request should reach the API")

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(_handler), base_url="https://cloud.lambda.ai"
    ) as http_client:
        client = LambdaClient(
            "test-key", http_client=http_client, allow_write=False, sleep=_no_sleep
        )
        with pytest.raises(WritesDisabledError):
            await client.request("POST", "/api/v1/instance-operations/terminate")
