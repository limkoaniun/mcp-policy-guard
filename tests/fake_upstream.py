"""A stand-in upstream MCP server for testing how the proxy handles failure.

`--die-on-call` makes it exit abruptly the moment a tool is invoked, without
answering -- the crude form of "the upstream subprocess dies mid-task", which
spec 10 says must fail the run loudly rather than degrade into ordinary tool
errors the agent shrugs off while `state.json` quietly freezes.

It is a test fixture, not part of the shipped package, which is why it lives
under `tests/` and takes the same `--run-dir` argument the CLI appends to
every upstream command.
"""

from __future__ import annotations

import argparse
import os

from mcp.server import MCPServer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--overlay", default=None)
    parser.add_argument("--die-on-call", action="store_true")
    args = parser.parse_args(argv)

    server = MCPServer(name="fake-upstream")

    @server.tool()
    def read_thing(code: str = "") -> str:
        """Read a thing."""
        if args.die_on_call:
            # No exception, no shutdown, no reply: the process is simply gone,
            # exactly as a killed or crashed subprocess would be.
            os._exit(70)
        return f"thing {code}"

    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
