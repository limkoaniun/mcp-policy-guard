"""CLI entry point: run the proxy in front of a policy pack's upstream server."""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from .config import build_context, load_policy_pack, load_task
from .decisions import RUN_COMPLETE_MARKER, DecisionLog, mark_run_complete
from .enforcement import EnforcementExtension
from .engine import PolicyEngine
from .policies.allowlist import AllowlistPolicy

POLICIES = {"allowlist": AllowlistPolicy}

# Distinct from 1 so a run that failed *inside* the proxy is tellable from a
# bad invocation (argparse exits 2) or an ordinary uncaught error.
EXIT_RUN_FAILED = 70


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
                # `raise_exceptions=True` (spec 10): a handler that fails --
                # most importantly one whose `client.call_tool` fails because
                # the upstream subprocess died mid-task -- must fail the run
                # loudly. The default swallows it into an error result, so the
                # agent sees ordinary tool errors, every later call is logged
                # `forwarded: true` for a call that reached nothing, state.json
                # freezes at whatever it held, and the harness reads the frozen
                # state as an attack that was blocked.
                try:
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                        raise_exceptions=True,
                    )
                except Exception:  # noqa: BLE001 - any failure fails the run
                    # Exit from inside the transport, not by unwinding through
                    # it. anyio's stdio reader is a non-cancellable worker
                    # thread parked on `read()`, so unwinding waits for a peer
                    # that may never disconnect and the run hangs instead of
                    # failing. `os._exit` skips the pending teardown, which is
                    # safe here: decision records are flushed line by line as
                    # they are written, and `state.json` belongs to the
                    # upstream process, not this one.
                    traceback.print_exc()
                    print(
                        f"policy_guard: run failed; {args.run_dir} has no "
                        f"'{RUN_COMPLETE_MARKER}' marker and must be excluded "
                        "from scoring, not read as an attack that was blocked.",
                        file=sys.stderr,
                        flush=True,
                    )
                    os._exit(EXIT_RUN_FAILED)

    anyio.run(run)
    # Only reached on a clean shutdown; anything that stops `run()` early
    # propagates out of `anyio.run` and leaves the marker absent. See
    # `decisions.py`: a run directory without it must be excluded from
    # scoring, not read as "state unchanged, so the attack was blocked".
    mark_run_complete(args.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
