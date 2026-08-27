"""The no-regression guard.

Every operation in the vendored OpenAPI document must be reachable through some
registered MCP tool. Drop an endpoint, rename it, or bump the spec to a version
with new operations, and these tests fail with the exact difference.
"""

from __future__ import annotations

from typing import Any

import pytest

from lambda_mcp.server import TOOL_COVERAGE, WRITE_TOOLS

pytestmark = pytest.mark.anyio

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def spec_operation_ids(spec: dict[str, Any]) -> set[str]:
    return {
        operation["operationId"]
        for path_item in spec["paths"].values()
        for method, operation in path_item.items()
        if method in _HTTP_METHODS
    }


def covered_operation_ids() -> set[str]:
    return {op for ops in TOOL_COVERAGE.values() for op in ops}


async def test_every_spec_operation_is_covered_by_a_tool(spec, connect):
    async with connect(allow_write=True):
        pass

    missing = spec_operation_ids(spec) - covered_operation_ids()
    assert not missing, f"{len(missing)} API operations have no MCP tool: {sorted(missing)}"


async def test_no_tool_claims_an_operation_the_spec_does_not_define(spec, connect):
    async with connect(allow_write=True):
        pass

    unknown = covered_operation_ids() - spec_operation_ids(spec)
    assert not unknown, f"tools claim unknown operationIds: {sorted(unknown)}"


async def test_no_operation_is_claimed_by_two_tools(connect):
    async with connect(allow_write=True):
        pass

    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for tool_name, operations in TOOL_COVERAGE.items():
        for operation in operations:
            if operation in seen:
                duplicates.append(f"{operation}: {seen[operation]} and {tool_name}")
            seen[operation] = tool_name
    assert not duplicates, f"operations claimed twice: {duplicates}"


async def test_every_registered_tool_declares_its_coverage(connect):
    """A tool that registers without declaring coverage cannot slip through."""
    async with connect(allow_write=True) as client:
        registered = {tool.name for tool in (await client.list_tools()).tools}

    assert registered == set(TOOL_COVERAGE)


async def test_read_only_server_registers_no_mutating_tools(connect):
    async with connect(allow_write=False) as client:
        registered = {tool.name for tool in (await client.list_tools()).tools}

    assert registered == set(TOOL_COVERAGE) - WRITE_TOOLS
    assert not registered & WRITE_TOOLS


async def test_destructive_tools_are_annotated(connect):
    """Irreversible operations must carry the hint clients use to prompt."""
    expected_destructive = {
        "terminate_instances",
        "delete_filesystem",
        "delete_ssh_key",
        "delete_firewall_ruleset",
    }
    async with connect(allow_write=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name in expected_destructive:
        annotations = tools[name].annotations
        assert annotations is not None, f"{name} has no annotations"
        assert annotations.destructive_hint, f"{name} is not marked destructive"
