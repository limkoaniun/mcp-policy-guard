"""CLI entry point: run the proxy in front of a policy pack's upstream server."""

from __future__ import annotations

import argparse

from .config import build_context, load_policy_pack, load_task
from .decisions import DecisionLog
from .enforcement import EnforcementExtension
from .engine import PolicyEngine
from .policies.allowlist import AllowlistPolicy

POLICIES = {"allowlist": AllowlistPolicy}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the MCP policy proxy.")
    parser.add_argument("--task", required=True)
    parser.add_argument("--policy-pack", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--approval-mode", choices=["deny", "approve"], default="deny")
    parser.add_argument("--enforcement", choices=["on", "off"], default="on")
    args = parser.parse_args(argv)

    task = load_task(args.task)
    pack = load_policy_pack(args.policy_pack)
    engine = PolicyEngine([POLICIES[name]() for name in pack.policies])
    enforcement = EnforcementExtension(
        engine,
        build_context(task),
        DecisionLog(f"{args.run_dir}/decisions.jsonl"),
        enforcement=args.enforcement == "on",
        approval_mode=args.approval_mode,
    )

    import anyio
    from mcp.server.stdio import stdio_server

    from .proxy import build_proxy_server
    from .upstream import connect_subprocess

    # The pack names the upstream command and its fixed arguments; the run's
    # own directory and (optionally) this task's attack overlay are run-time
    # specifics the pack cannot know in advance, so they are appended here.
    # The enrolment server (this slice's only upstream) refuses to start
    # without a run directory -- see `enrolment/server.py` for why a missing
    # one is rejected outright rather than falling back to an ephemeral run.
    upstream_args = [*pack.upstream_args, "--run-dir", args.run_dir]
    if task.overlay:
        upstream_args += ["--overlay", task.overlay]

    async def run() -> None:
        async with connect_subprocess(pack.upstream_command, upstream_args) as client:
            server = await build_proxy_server(client, enforcement)
            # `build_proxy_server` returns a low-level `mcp.server.Server`
            # (see `proxy.py` for why), which speaks stdio via the
            # `stdio_server` stream pair rather than the higher-level
            # `MCPServer.run_stdio_async`.
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
