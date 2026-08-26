"""CLI entry point: run the proxy in front of a policy pack's upstream server."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

from .config import build_context, load_policy_pack, load_task
from .decisions import (
    RUN_COMPLETE_MARKER,
    DecisionLog,
    mark_run_complete,
    write_run_manifest,
)
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
    from .upstream import UpstreamError, connect_subprocess

    # The pack names the upstream command and its fixed arguments; the run's
    # own directory and (optionally) this task's attack overlay are run-time
    # specifics the pack cannot know in advance, so they are appended here.
    # The enrolment server (this slice's only upstream) refuses to start
    # without a run directory -- see `enrolment/server.py` for why a missing
    # one is rejected outright rather than falling back to an ephemeral run.
    upstream_args = [*pack.upstream_args, "--run-dir", args.run_dir]
    # One source for "was an overlay delivered", used both to build the
    # upstream's arguments and to record the fact, so the two cannot disagree.
    overlay = task.overlay or None
    if overlay:
        upstream_args += ["--overlay", overlay]

    upstream_command = resolve_upstream_command(pack.upstream_command)

    # Before anything can go wrong: a run that fails still has to say which arm
    # it was. Nothing else in the run directory records that, and for a run in
    # which no call happened to be denied the enforcement-on and
    # enforcement-off logs are byte-compatible. See `RunManifest`.
    write_run_manifest(
        args.run_dir,
        task_id=task.task_id,
        task_path=str(Path(args.task).resolve()),
        policy_pack_path=str(Path(args.policy_pack).resolve()),
        policies=list(pack.policies),
        # Read back off the extension rather than off `args`, so the manifest
        # records the arm that is actually in force rather than the one that
        # was asked for. The two are derived from the same flag today; a
        # manifest that re-derives it from the flag could not notice if they
        # ever came apart, and a recorded arm that silently disagrees with the
        # enforced one is the worst version of this defect.
        enforcement="on" if enforcement.enforcement else "off",
        approval_mode=enforcement.approval_mode,
        overlay=str(Path(overlay).resolve()) if overlay else None,
        upstream_command=upstream_command,
        upstream_args=list(upstream_args),
    )

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

            # The transport loop ended cleanly -- but that only means the agent
            # disconnected, and a *denied* call short-circuits in
            # `PolicyProxy.call` without ever touching the upstream. So a run
            # whose remaining calls were all denied never asks the upstream
            # anything, and an upstream that died along the way goes unseen.
            # In an attack scenario that is the normal sequence, because the
            # attack call is the denied call: upstream dies, every later call
            # is DENY, the agent disconnects, and the run certifies itself
            # scoreable for a defence that was never tested against a live
            # server. Ask the upstream one question before vouching for it.
            try:
                # `cache_mode="bypass"`: the default ("use") would let a server
                # that advertises a `tools/list` cache hint serve this probe
                # from the client's own response cache -- which is warm in a
                # real run because the agent's own `tools/list` populated it
                # -- and a cache hit against a dead process would defeat the
                # whole point of asking. The round trip has to be guaranteed
                # by us, not left to the upstream's discretion.
                await client.list_tools(cache_mode="bypass")
            except Exception as error:
                raise UpstreamError(
                    "upstream was not alive at the end of the run, so this run "
                    f"is not a result: {error}"
                ) from error

    try:
        anyio.run(run)
    except Exception:  # noqa: BLE001 - any failure fails the run
        # Reached by the end-of-run probe above; the in-transport failure path
        # exits before it can unwind this far. Unwinding is safe here because
        # the transport is already closed -- the reader thread that made
        # `os._exit` necessary inside the loop is gone by this point.
        traceback.print_exc()
        print(
            f"policy_guard: run failed; {args.run_dir} has no "
            f"'{RUN_COMPLETE_MARKER}' marker and must be excluded "
            "from scoring, not read as an attack that was blocked.",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_RUN_FAILED
    # Only reached on a clean shutdown with the upstream still answering;
    # anything that stops `run()` early leaves the marker absent. See
    # `decisions.py`: a run directory without it must be excluded from
    # scoring, not read as "state unchanged, so the attack was blocked".
    mark_run_complete(args.run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
