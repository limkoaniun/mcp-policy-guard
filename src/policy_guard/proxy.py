"""The MCP policy proxy.

    agent  <-- MCP -->  PolicyProxy  <-- MCP -->  tool servers

The proxy mirrors the upstream's tool list to the agent **unfiltered** and
enforces at call time. Filtering the list would be the more obviously secure
choice and the wrong one for this project: an attack that fails because the
tool was absent is indistinguishable from one the model refused, and attack
success rate would silently measure tool availability instead of the defence.

`PolicyProxy` holds the transport-independent behaviour and is what the tests
drive. `build_proxy_server` wraps it in an MCP server for real runs.

Tool mirroring uses the low-level `mcp.server.Server`, constructed with
explicit `on_list_tools` / `on_call_tool` handlers, rather than `MCPServer`.
`MCPServer.add_tool` infers a JSON schema from a Python function signature,
which a generic forwarder does not have -- there is no Python signature for
an arbitrary upstream tool. The low-level `Server` instead lets the upstream's
`Tool` objects (schemas included) pass through untouched, which is what a
proxy should do: the agent sees exactly what the upstream published, never a
degraded stand-in.
"""

from __future__ import annotations

from mcp import types
from mcp.server import Server

from .enforcement import EnforcementExtension
from .models import ToolCall


class PolicyProxy:
    def __init__(self, client, enforcement: EnforcementExtension):
        self.client = client
        self.enforcement = enforcement

    async def mirrored_tools(self) -> list[types.Tool]:
        """The upstream's tools, verbatim, schemas included."""
        return list((await self.client.list_tools()).tools)

    async def call(self, name: str, arguments: dict) -> object:
        """Enforce, then forward or refuse. A refused call never reaches upstream."""
        decision, forward = self.enforcement.decide(ToolCall(tool=name, arguments=arguments))
        if not forward:
            return self.enforcement.refusal(decision)
        return await self.client.call_tool(name, arguments)


async def build_proxy_server(client, enforcement: EnforcementExtension) -> Server:
    """Wrap a `PolicyProxy` in a low-level MCP `Server` for real transports.

    `on_list_tools` and `on_call_tool` are the low-level `Server`'s constructor
    hooks; both delegate straight to the transport-independent `PolicyProxy`.
    """
    proxy = PolicyProxy(client, enforcement)

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await proxy.mirrored_tools())

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await proxy.call(params.name, dict(params.arguments or {}))

    return Server(
        "policy-guard-proxy",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
