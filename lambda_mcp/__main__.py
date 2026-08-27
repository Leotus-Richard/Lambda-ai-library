"""Entry point: ``python -m lambda_mcp`` / ``lambda-mcp``.

Reads configuration from the environment and serves over stdio.

Note that stdout carries the MCP protocol, so every diagnostic here goes to
stderr. Printing to stdout would corrupt the session.
"""

from __future__ import annotations

import os
import sys

from lambda_mcp.client import DEFAULT_BASE_URL, LambdaClient
from lambda_mcp.server import TOOL_COVERAGE, WRITE_TOOLS, build_server

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def main() -> int:
    api_key = os.environ.get("LAMBDA_API_KEY", "").strip()
    if not api_key:
        print(
            "LAMBDA_API_KEY is not set. Generate a key at "
            "https://cloud.lambda.ai/api-keys and export it before starting "
            "the server.",
            file=sys.stderr,
        )
        return 1

    allow_write = _enabled(os.environ.get("LAMBDA_MCP_ALLOW_WRITE"))
    base_url = os.environ.get("LAMBDA_API_BASE", "").strip() or DEFAULT_BASE_URL

    client = LambdaClient(api_key, base_url=base_url, allow_write=allow_write)
    server = build_server(client, allow_write=allow_write)

    registered = len(TOOL_COVERAGE) - (0 if allow_write else len(WRITE_TOOLS))
    mode = "read/write" if allow_write else "read-only"
    print(
        f"lambda-mcp serving {registered} tools ({mode}) against {base_url}",
        file=sys.stderr,
    )

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
