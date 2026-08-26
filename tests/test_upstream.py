import sys

import pytest
from mcp.server import MCPServer

from policy_guard.upstream import UpstreamError, connect_in_process, connect_subprocess


def build_server() -> MCPServer:
    server = MCPServer(name="fixture")

    @server.tool()
    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    return server


@pytest.mark.anyio
async def test_lists_the_upstream_tools():
    async with connect_in_process(build_server()) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == {"echo", "add"}


@pytest.mark.anyio
async def test_calls_an_upstream_tool():
    async with connect_in_process(build_server()) as client:
        result = await client.call_tool("add", {"a": 2, "b": 3})
    assert "5" in str(result.content)


@pytest.mark.anyio
async def test_tool_descriptions_survive_the_connection():
    """The proxy mirrors descriptions, so they must arrive intact."""
    async with connect_in_process(build_server()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert "unchanged" in (tools["echo"].description or "")


@pytest.mark.anyio
async def test_subprocess_upstream_lists_tools(tmp_path):
    script = tmp_path / "fixture_server.py"
    script.write_text(
        "from mcp.server import MCPServer\n"
        "s = MCPServer(name='sub')\n"
        "@s.tool()\n"
        "def ping() -> str:\n"
        "    '''Reply pong.'''\n"
        "    return 'pong'\n"
        "s.run('stdio')\n"
    )
    async with connect_subprocess(sys.executable, [str(script)]) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == {"ping"}


def _flatten(error: BaseException) -> list[BaseException]:
    """Unpack nested BaseExceptionGroups (anyio's TaskGroup teardown wraps in one)."""
    if isinstance(error, BaseExceptionGroup):
        flattened: list[BaseException] = []
        for sub in error.exceptions:
            flattened.extend(_flatten(sub))
        return flattened
    return [error]


@pytest.mark.anyio
async def test_caller_error_inside_the_block_propagates_unwrapped():
    """A bug in the caller's own code is not a connectivity failure.

    connect_in_process must not relabel it as an UpstreamError - that would
    send someone debugging the wrong component. (mcp's ClientSession tears
    down its in-process session inside an anyio TaskGroup, which wraps a body
    exception in a BaseExceptionGroup; what matters here is that UpstreamError
    is not among the leaves, and the original ValueError is still reachable.)
    """
    with pytest.raises(BaseException) as excinfo:
        async with connect_in_process(build_server()):
            raise ValueError("a caller's own bug, nothing to do with the upstream")

    leaves = _flatten(excinfo.value)
    assert not any(isinstance(leaf, UpstreamError) for leaf in leaves)
    assert any(
        isinstance(leaf, ValueError) and "a caller's own bug" in str(leaf) for leaf in leaves
    )


@pytest.mark.anyio
async def test_connect_failure_still_wraps_with_cause():
    """A genuine connect failure still raises UpstreamError with the cause reachable."""
    with pytest.raises(UpstreamError) as excinfo:
        async with connect_in_process(42):  # not a Server/MCPServer/Transport/str
            pass
    assert excinfo.value.__cause__ is not None
