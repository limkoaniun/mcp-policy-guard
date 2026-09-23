"""CLI entry point: run the proxy in front of a policy pack's upstream server."""

from __future__ import annotations

import argparse
import importlib
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
from .policies.arg_validation import ArgumentValidationPolicy
from .policies.taint import TaintPolicy


def _load_domain_rules(pack, run_dir=None) -> dict:
    """Resolve the pack's `domain_rules` to a validator mapping.

    The pack names the module by import path; this is the one place the
    generic layer resolves it, at run time, so `policy_guard` never carries a
    static import of a domain package. `pack.domain_rules_args` supplies
    whatever else the factory needs -- where the domain's data lives -- so
    that configuration has one home (the pack) rather than being duplicated
    into environment variables nothing keeps in sync with it. `run_dir` is
    passed through separately because it is a run-time fact the pack cannot
    know in advance, the same way `__main__` already appends it to the
    upstream's own arguments.

    A malformed `domain_rules` value raises `ValueError` naming the pack, the
    value, and what went wrong, rather than letting a bare `AttributeError`
    or `ModuleNotFoundError` reach the operator with no context.
    """
    if not pack.domain_rules:
        return {}

    module_name, sep, attribute = pack.domain_rules.partition(":")
    if not sep or not module_name or not attribute:
        raise ValueError(
            f"policy pack {pack.name!r} names domain_rules "
            f"{pack.domain_rules!r}, which is not a 'module:callable' "
            "import path"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise ValueError(
            f"policy pack {pack.name!r} names domain_rules "
            f"{pack.domain_rules!r}: module {module_name!r} could not be "
            f"imported ({error})"
        ) from error
    try:
        factory = getattr(module, attribute)
    except AttributeError as error:
        raise ValueError(
            f"policy pack {pack.name!r} names domain_rules "
            f"{pack.domain_rules!r}: module {module_name!r} has no attribute "
            f"{attribute!r}"
        ) from error
    try:
        return factory(**pack.domain_rules_args, run_dir=run_dir)
    except TypeError as error:
        # A pack whose `domain_rules_args` does not match the factory's
        # parameters -- commonly, a `domain_rules` set with no
        # `domain_rules_args` at all -- would otherwise surface as a bare
        # `TypeError` naming neither the pack nor the value at fault, which
        # this function's own docstring promises not to do.
        raise ValueError(
            f"policy pack {pack.name!r} names domain_rules "
            f"{pack.domain_rules!r} with domain_rules_args "
            f"{pack.domain_rules_args!r}, which does not match what "
            f"{attribute!r} in {module_name!r} accepts: {error}"
        ) from error


# Each entry takes the loaded pack and the run directory, so a policy may
# draw configuration from either. Policies that need neither simply ignore
# the arguments.
POLICIES = {
    "allowlist": lambda pack, run_dir: AllowlistPolicy(),
    "arg_validation": lambda pack, run_dir: ArgumentValidationPolicy(
        principal_argument=pack.principal_argument,
        validators=_load_domain_rules(pack, run_dir=run_dir),
        exempt_tools=pack.principal_exempt_tools,
    ),
    "taint": lambda pack, run_dir: TaintPolicy(pack.side_effecting_tools),
}

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
    if (
        args.enforcement == "on"
        and pack.principal_argument
        and "arg_validation" in pack.policies
        and not task.principal_id
    ):
        # `ArgumentValidationPolicy` correctly returns "no opinion" when a
        # pack declares `principal_argument` but the task binds no
        # `principal_id` -- some tasks legitimately are not principal-scoped.
        # But that is indistinguishable from a task author who simply forgot
        # to set `principal_id`, and in that case principal binding is
        # silently inert: every call is treated as unscoped, no call is ever
        # denied for naming the wrong principal, and `decisions.jsonl` looks
        # exactly like a normal enforced run. Same species of failure as
        # `PolicyPack._at_least_one_policy` in `config.py` guards against for
        # an empty `policies:` list -- a failure presenting as a confident
        # success, biased toward the defence, must fail loudly at load time
        # rather than silently.
        raise ValueError(
            f"policy pack {pack.name!r} declares principal_argument "
            f"{pack.principal_argument!r} and loads arg_validation, but task "
            f"{task.task_id!r} sets no principal_id. Without one, principal "
            "binding is inert -- no call would ever be denied for naming the "
            "wrong principal -- and this run would be indistinguishable in "
            "decisions.jsonl from a genuinely principal-scoped enforced run. "
            "Set principal_id on the task, or drop arg_validation/"
            "principal_argument if this task is not meant to be "
            "principal-scoped."
        )
    engine = PolicyEngine([POLICIES[name](pack, args.run_dir) for name in pack.policies])
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
        # The pack's trust labels are per tool. Only the untrusted set matters
        # to taint, and it is derived here so the pack stays the one source.
        untrusted_tools=frozenset(
            tool for tool, label in pack.trust.items() if label == "untrusted"
        ),
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
    # If the pack's `domain_rules_args` names a `students_path`, append it as
    # the upstream's own `--students` too. Nothing else reconciled these:
    # left to their separate defaults, `_load_domain_rules`'s validators and
    # the upstream's own `RunState` could silently judge two different
    # student files, agreeing only by coincidence -- and if they diverged
    # nothing would crash, the validators would just be checking records
    # nobody was actually enrolling into. Deriving the flag from the same
    # pack field this way makes the two agree by construction. A pack with
    # no `students_path` in `domain_rules_args` is unaffected: the upstream
    # keeps its own default.
    domain_records_path = pack.domain_rules_args.get("students_path")
    if domain_records_path:
        upstream_args += ["--students", domain_records_path]
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
