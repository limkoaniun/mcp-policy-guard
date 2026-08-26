"""Connecting to an upstream MCP server.

Two transports, deliberately behind one interface:

- in-process, used by the tests, which keeps them fast and network-free
- subprocess over stdio, used in real runs, where a fresh process per scenario
  gives state isolation for nothing and leaves no reset tool for an attacker

Nothing here knows what the upstream serves. The domain arrives through
configuration, never through this module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client


class UpstreamError(RuntimeError):
    """The upstream server could not be reached, started, or kept alive."""


@asynccontextmanager
async def connect_in_process(server) -> AsyncIterator[Client]:
    try:
        async with Client(server) as client:
            yield client
    except Exception as error:
        raise UpstreamError(f"in-process upstream failed: {error}") from error


@asynccontextmanager
async def connect_subprocess(
    command: str, args: list[str], cwd: str | None = None
) -> AsyncIterator[Client]:
    parameters = StdioServerParameters(command=command, args=args, cwd=cwd)
    try:
        async with Client(stdio_client(parameters)) as client:
            yield client
    except Exception as error:
        raise UpstreamError(
            f"upstream subprocess failed ({command} {' '.join(args)}): {error}"
        ) from error
