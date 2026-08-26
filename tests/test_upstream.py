import sys

import pytest
from mcp.server import MCPServer

from policy_guard.upstream import connect_in_process, connect_subprocess


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
