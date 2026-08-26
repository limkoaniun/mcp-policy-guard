"""A stand-in upstream MCP server for testing how the proxy handles failure.

Two ways of dying, because the proxy notices them by different means:

- `--die-on-call` exits the moment a tool is invoked, without answering -- the
  crude form of "the upstream subprocess dies mid-task", which spec 10 says
  must fail the run loudly rather than degrade into ordinary tool errors the
  agent shrugs off while `state.json` quietly freezes. The proxy sees this
  one because the death happens *inside* a forwarded call.
- `--die-after-call` answers the call and *then* vanishes. Nothing raises, so
  the proxy learns nothing; if every later call is denied by policy, the proxy
  never speaks to the upstream again and the death goes unobserved. That is the
  shape of a run in which the attack call is the denied call, and it is why the
  proxy probes the upstream before it certifies a run as scoreable.

It is a test fixture, not part of the shipped package, which is why it lives
under `tests/` and takes the same `--run-dir` argument the CLI appends to
every upstream command.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import threading
import time

from mcp.server import CacheHint, MCPServer

# Written into the run directory immediately before `--die-after-call` exits,
# so a test can wait for the death instead of sleeping and hoping.
DEAD_MARKER = "upstream_dead"


def _die_once_the_reply_is_out(run_dir: str) -> None:
    """Exit shortly after the current call has been answered.

    The delay is only there to let the reply reach the proxy: the point of this
    mode is a death that no forwarded call reports, so the call that triggers
    it has to succeed first.
    """

    def die() -> None:
        time.sleep(0.5)
        pathlib.Path(run_dir, DEAD_MARKER).write_text("dead\n", encoding="utf-8")
        os._exit(3)

    threading.Thread(target=die, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--overlay", default=None)
    parser.add_argument("--die-on-call", action="store_true")
    parser.add_argument("--die-after-call", action="store_true")
    # Set only by the test proving the end-of-run liveness probe cannot be
    # satisfied from the client's response cache: a `tools/list` cache hint
    # is entirely this (the upstream's) choice to make, and one warmed by the
    # agent's own `tools/list` call must not let a dead process's reply stand
    # in for the probe's round trip.
    parser.add_argument("--tools-list-ttl-ms", type=int, default=None)
    args = parser.parse_args(argv)

    cache_hints = (
        {"tools/list": CacheHint(ttl_ms=args.tools_list_ttl_ms)}
        if args.tools_list_ttl_ms is not None
        else None
    )
    server = MCPServer(name="fake-upstream", cache_hints=cache_hints)

    @server.tool()
    def read_thing(code: str = "") -> str:
        """Read a thing."""
        if args.die_on_call:
            # No exception, no shutdown, no reply: the process is simply gone,
            # exactly as a killed or crashed subprocess would be.
            # A distinct code from the CLI's own EXIT_RUN_FAILED, so a test
            # asserting on the proxy's exit cannot be reading this one.
            os._exit(3)
        if args.die_after_call:
            _die_once_the_reply_is_out(args.run_dir)
        return f"thing {code}"

    @server.tool()
    def destroy_thing(code: str = "") -> str:
        """Destroy a thing.

        Present on the mirrored tool list but never declared by the tasks that
        use this fixture, so the allowlist policy denies it -- the stand-in for
        an attack call.
        """
        return f"destroyed {code}"

    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
