"""Policy enforcement at the tool-call boundary.

`decide()` holds all the logic and touches no MCP types, so the enforcement
rules are testable without a protocol in sight. `refusal()` is the one MCP type
it produces, and only for a call it blocks.

There is no adapter class here. What ships is `PolicyProxy.call` in `proxy.py`:
it calls `decide()`, returns `refusal()` when the verdict says not to forward,
and otherwise forwards to the upstream. `build_proxy_server`'s low-level
`on_call_tool` handler delegates to that and nothing else. Anyone adding an
alternative entry point must carry over `PolicyProxy.call`'s re-raise of
transport failures as `UpstreamError` -- an entry point that swallows them
restores the bug where a dead upstream reads as an attack that was blocked.

Three behaviours are load-bearing:

- A DENY short-circuits: `call_next` is never invoked, so the upstream server
  never sees the call. That is the property a per-endpoint validator inside the
  upstream could not give us.
- A policy that raises denies. An enforcement layer that cannot decide must not
  thereby permit.
- With `enforcement=False` the verdict is still computed and logged, and the
  call is forwarded anyway. The with/without comparison is therefore one code
  path differing only in whether the verdict is acted on.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from mcp.types import CallToolResult, TextContent

from .decisions import DecisionLog
from .engine import PolicyEngine
from .models import Decision, TaskContext, ToolCall, Verdict


class EnforcementExtension:
    def __init__(
        self,
        engine: PolicyEngine,
        context: TaskContext,
        log: DecisionLog,
        *,
        enforcement: bool = True,
        approval_mode: str = "deny",
        untrusted_tools: Iterable[str] = (),
    ):
        self.engine = engine
        self.context = context
        self.log = log
        self.enforcement = enforcement
        self.approval_mode = approval_mode
        # The pack's trust labels, reduced to the set that matters here. A
        # forwarded call to any of these marks the run tainted; see
        # `observe_forwarded`.
        self.untrusted_tools = frozenset(untrusted_tools)

    def decide(self, call: ToolCall) -> tuple[Decision, bool]:
        """Rule on one call. Returns the decision and whether to forward it."""
        started = time.perf_counter()
        try:
            decision = self.engine.evaluate(call, self.context)
        except Exception as error:  # noqa: BLE001 - a policy that cannot decide must deny
            decision = Decision(
                verdict=Verdict.DENY,
                policy="engine",
                reason=f"policy raised, so the call is denied: {error}",
            )

        approval_mode = None
        if decision.verdict is Verdict.REQUIRE_APPROVAL:
            approval_mode = self.approval_mode
            permitted = self.approval_mode == "approve"
        else:
            permitted = decision.verdict is Verdict.ALLOW

        forward = True if not self.enforcement else permitted
        # `forwarded` records the *decision* to forward, not the fact of it:
        # this write happens before `PolicyProxy.call` attempts
        # `client.call_tool`, so a call the upstream never received is still
        # logged `forwarded: true`. Reading it as "the upstream saw this" is
        # wrong. Splitting it into a decision field and an outcome field needs
        # the post-call hook and belongs to a later slice; until then the loud
        # failure on a dead upstream (`raise_exceptions=True` at the
        # `Server.run` call site) and the end-of-run marker are what stop a
        # truncated run from being scored.
        self.log.record(
            call,
            decision,
            task_id=self.context.task_id,
            forwarded=forward,
            latency_ms=(time.perf_counter() - started) * 1000,
            tainted=self.context.tainted,
            approval_mode=approval_mode,
        )
        return decision, forward

    def observe_forwarded(self, call: ToolCall) -> None:
        """Record that `call` was forwarded to the upstream and answered.

        Called by `PolicyProxy.call` after the upstream returns, whether the
        result was an error or not: a forwarded call to an untrusted tool
        taints the run even if the document was empty, because fail-closed is
        the direction this layer errs in. A *refused* call is never observed
        -- nothing was read. Taint is sticky for the life of this extension,
        which is one proxy process: one campaign run, or one demo message.
        """
        if call.tool not in self.untrusted_tools:
            return
        if call.tool not in self.context.taint_sources:
            self.context.taint_sources.append(call.tool)
        self.context.tainted = True

    @staticmethod
    def refusal(decision: Decision) -> CallToolResult:
        """What the agent receives when a call is blocked.

        A CallToolResult rather than a plain dict because `tools/call` results
        are validated against that model on the way out - a bare dict is
        rejected and the agent gets a generic internal error instead of the
        reason. The reason is included verbatim: the agent relays it to the
        student, so a block reads as an explanation rather than a failure.
        """
        return CallToolResult(
            content=[TextContent(type="text", text=decision.reason)],
            isError=True,
        )
