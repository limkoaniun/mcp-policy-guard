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
from .upstream import UpstreamError


class PolicyProxy:
    def __init__(self, client, enforcement: EnforcementExtension):
        self.client = client
        self.enforcement = enforcement

    async def mirrored_tools(self) -> list[types.Tool]:
        """The upstream's tools, verbatim, schemas included."""
        try:
            return list((await self.client.list_tools()).tools)
        except Exception as error:
            raise UpstreamError(f"upstream list_tools failed: {error}") from error

    async def call(self, name: str, arguments: dict) -> object:
        """Enforce, then forward or refuse. A refused call never reaches upstream."""
        call = ToolCall(tool=name, arguments=arguments)
        decision, forward = self.enforcement.decide(call)
        if not forward:
            return self.enforcement.refusal(decision)
        try:
            result = await self.client.call_tool(name, arguments)
        except Exception as error:
            # A tool that *fails* returns a `CallToolResult` with `is_error`
            # set; it does not raise. So an exception here is not a tool
            # failure -- it is the session or the subprocess itself being gone,
            # which spec 10 says must fail the run loudly.
            #
            # Re-raising as `UpstreamError` is load-bearing, not cosmetic. The
            # SDK's request loop maps an `MCPError` (what a dead session
            # raises) straight to a wire error *before* it consults
            # `raise_exceptions`, so `raise_exceptions=True` on its own leaves
            # a dead upstream looking like an ordinary tool error: the agent
            # carries on, later calls are still logged `forwarded: true` for
            # calls that reached nothing, `state.json` freezes, and the harness
            # scores the frozen state as an attack that was blocked. An
            # exception the SDK has no wire mapping for is the one that
            # actually escapes and takes the run down with it.
            raise UpstreamError(
                f"upstream call to {name!r} failed, so this run is not a result: {error}"
            ) from error
        # After the upstream answered, not before: a call the upstream never
        # saw must not taint the run.
        self.enforcement.observe_forwarded(call)
        return result


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
