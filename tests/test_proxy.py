import pytest
from mcp import Client
from mcp.server import MCPServer

from policy_guard.decisions import DecisionLog
from policy_guard.enforcement import EnforcementExtension
from policy_guard.engine import PolicyEngine
from policy_guard.models import TaskContext
from policy_guard.policies.allowlist import AllowlistPolicy
from policy_guard.proxy import PolicyProxy, build_proxy_server
from policy_guard.upstream import UpstreamError, connect_in_process


class RecordingUpstream:
    """Records every call that actually reached it."""

    def __init__(self):
        self.received: list[tuple[str, dict]] = []
        self.server = MCPServer(name="upstream")
        upstream = self

        @self.server.tool()
        def read_thing(code: str) -> str:
            """Read a thing."""
            upstream.received.append(("read_thing", {"code": code}))
            return f"thing {code}"

        @self.server.tool()
        def destroy_thing(code: str) -> str:
            """Destroy a thing. Side-effecting."""
            upstream.received.append(("destroy_thing", {"code": code}))
            return f"destroyed {code}"


def enforcement_for(allowed, log=None, enforcement=True):
    return EnforcementExtension(
        PolicyEngine([AllowlistPolicy()]),
        TaskContext(task_id="T", allowed_tools=frozenset(allowed)),
        log or DecisionLog(None),
        enforcement=enforcement,
    )


@pytest.mark.anyio
async def test_the_full_upstream_tool_list_is_mirrored():
    """Unfiltered by design: hiding a tool would make an attack fail for the wrong reason."""
    up = RecordingUpstream()
    async with connect_in_process(up.server) as client:
        proxy = PolicyProxy(client, enforcement_for({"read_thing"}))
        names = {tool.name for tool in await proxy.mirrored_tools()}
    assert names == {"read_thing", "destroy_thing"}


@pytest.mark.anyio
async def test_mirrored_tools_keep_their_input_schemas():
    up = RecordingUpstream()
    async with connect_in_process(up.server) as client:
        proxy = PolicyProxy(client, enforcement_for({"read_thing"}))
        tools = {tool.name: tool for tool in await proxy.mirrored_tools()}
    # This SDK's `Tool` model exposes the field as `input_schema` (its wire
    # alias is `inputSchema`, but that alias is not a readable attribute).
    assert "code" in str(tools["destroy_thing"].input_schema)


@pytest.mark.anyio
async def test_an_allowed_call_reaches_the_upstream():
    up = RecordingUpstream()
    async with connect_in_process(up.server) as client:
        proxy = PolicyProxy(client, enforcement_for({"read_thing"}))
        await proxy.call("read_thing", {"code": "X"})
    assert up.received == [("read_thing", {"code": "X"})]


@pytest.mark.anyio
async def test_a_denied_call_never_reaches_the_upstream():
    """The property the whole design rests on."""
    up = RecordingUpstream()
    async with connect_in_process(up.server) as client:
        proxy = PolicyProxy(client, enforcement_for({"read_thing"}))
        await proxy.call("destroy_thing", {"code": "X"})
    assert up.received == []


@pytest.mark.anyio
async def test_a_denied_call_returns_the_reason_to_the_agent():
    up = RecordingUpstream()
    async with connect_in_process(up.server) as client:
        proxy = PolicyProxy(client, enforcement_for({"read_thing"}))
        result = await proxy.call("destroy_thing", {"code": "X"})
    assert "destroy_thing" in str(result)


@pytest.mark.anyio
async def test_with_enforcement_off_the_denied_call_does_reach_the_upstream():
    up = RecordingUpstream()
    log = DecisionLog(None)
    async with connect_in_process(up.server) as client:
        proxy = PolicyProxy(client, enforcement_for({"read_thing"}, log, enforcement=False))
        await proxy.call("destroy_thing", {"code": "X"})
    assert up.received == [("destroy_thing", {"code": "X"})]
    assert log.records[0].verdict == "deny"


@pytest.mark.anyio
async def test_an_upstream_error_is_forwarded_and_logged_as_forwarded():
    """A tool that fails is a tool failure, not a policy decision.

    Recording it as forwarded=True is what stops a broken upstream from
    reading, in the metrics, like a defence that worked.
    """
    server = MCPServer(name="broken")

    @server.tool()
    def explode(code: str) -> str:
        """Always fails."""
        raise RuntimeError("upstream is broken")

    log = DecisionLog(None)
    async with connect_in_process(server) as client:
        proxy = PolicyProxy(client, enforcement_for({"explode"}, log))
        result = await proxy.call("explode", {"code": "X"})

    assert log.records[0].verdict == "allow"
    assert log.records[0].forwarded is True
    # `isError` is the wire alias and is not a readable attribute on this SDK's
    # model; `getattr(result, "isError", False)` was therefore always False,
    # leaving the whole assertion resting on its first disjunct. Assert on the
    # real attribute, and on both halves rather than either.
    assert result.is_error is True
    assert "broken" in str(result.content).lower()


@pytest.mark.anyio
async def test_a_failing_upstream_call_surfaces_rather_than_being_swallowed():
    """Spec 10: an upstream that dies mid-task must fail the run loudly.

    `PolicyProxy.call` awaits `client.call_tool`; if that raises -- a dead
    subprocess rather than a tool returning an error -- the exception must
    propagate to the caller. The shipped path then relies on
    `Server.run(..., raise_exceptions=True)` (see `__main__.py`) to carry it
    out of the process, instead of turning it into an ordinary tool error the
    agent shrugs off while `state.json` quietly freezes.
    """

    class DeadUpstream:
        async def list_tools(self):  # pragma: no cover - not exercised here
            raise UpstreamError("upstream subprocess is gone")

        async def call_tool(self, name, arguments):
            raise UpstreamError("upstream subprocess is gone")

    log = DecisionLog(None)
    proxy = PolicyProxy(DeadUpstream(), enforcement_for({"read_thing"}, log))
    with pytest.raises(UpstreamError, match="gone"):
        await proxy.call("read_thing", {"code": "X"})


@pytest.mark.anyio
async def test_the_proxy_server_does_not_swallow_a_failing_handler():
    """The same property one layer up, through the shipped `build_proxy_server`.

    A low-level `Server` run with the default `raise_exceptions=False` converts
    a handler exception into an error result. That is how a dead upstream came
    to look like an ordinary tool failure while every later call was still
    logged `forwarded: true`. `Client(..., raise_exceptions=True)` here is the
    client-side mirror of the `raise_exceptions=True` the CLI passes to
    `Server.run`, so a failure crosses the boundary instead of being absorbed.
    """

    class DeadUpstream:
        async def list_tools(self):  # pragma: no cover - not exercised here
            raise UpstreamError("upstream subprocess is gone")

        async def call_tool(self, name, arguments):
            raise UpstreamError("upstream subprocess is gone")

    server = await build_proxy_server(DeadUpstream(), enforcement_for({"read_thing"}))
    async with Client(server, raise_exceptions=True) as client:
        with pytest.raises(Exception, match="gone"):
            await client.call_tool("read_thing", {"code": "X"})
