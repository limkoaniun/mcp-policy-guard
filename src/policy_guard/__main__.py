"""CLI entry point: run the proxy in front of a policy pack's upstream server."""

from __future__ import annotations

import argparse
import sys

from .config import build_context, load_policy_pack, load_task
from .decisions import DecisionLog
from .enforcement import EnforcementExtension
from .engine import PolicyEngine
from .policies.allowlist import AllowlistPolicy

POLICIES = {"allowlist": AllowlistPolicy}


def resolve_upstream_command(command: str) -> str:
    """Resolve a bare interpreter name to the interpreter running this proxy.

    The upstream server needs this project's dependencies, so it has to run
    under the same interpreter as the proxy -- not whichever `python` happens
    to be on PATH, if any. A pack naming a specific executable is left alone.
    """
    return sys.executable if command in {"python", "python3"} else command


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
    if args.enforcement == "on" and not engine.policies:
        # Belt-and-braces: `PolicyPack` already rejects an empty `policies:`
        # list, so this cannot fire from a loaded pack today. It is here
        # because an enforcement-on run with an empty engine allows every call
        # while logging verdicts that look like enforcement, and that failure
        # would flatter the defence. It must never be reachable silently.
        raise ValueError(
            "--enforcement on but the policy engine holds no policies; "
            "every call would be allowed while the run looked enforced"
        )
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

    upstream_command = resolve_upstream_command(pack.upstream_command)

    async def run() -> None:
        async with connect_subprocess(upstream_command, upstream_args) as client:
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
