import pytest
from mcp.server import MCPServer

from policy_guard.decisions import DecisionLog
from policy_guard.enforcement import EnforcementExtension
from policy_guard.engine import PolicyEngine
from policy_guard.models import TaskContext
from policy_guard.policies.allowlist import AllowlistPolicy
from policy_guard.proxy import PolicyProxy
from policy_guard.upstream import connect_in_process


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
    assert "broken" in str(result).lower() or getattr(result, "isError", False)
