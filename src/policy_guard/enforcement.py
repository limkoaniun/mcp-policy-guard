"""Policy enforcement at the tool-call boundary.

`decide()` holds all the logic and touches no MCP types, so the enforcement
rules are testable without a protocol in sight. `intercept_tool_call` is the
thin MCP adapter over it.

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
    ):
        self.engine = engine
        self.context = context
        self.log = log
        self.enforcement = enforcement
        self.approval_mode = approval_mode

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
        self.log.record(
            call,
            decision,
            task_id=self.context.task_id,
            forwarded=forward,
            latency_ms=(time.perf_counter() - started) * 1000,
            tainted=getattr(self.context, "tainted", False),
            approval_mode=approval_mode,
        )
        return decision, forward

    async def intercept_tool_call(self, params, ctx, call_next):
        """MCP adapter over `decide`. Short-circuits without calling `call_next`."""
        call = ToolCall(tool=params.name, arguments=dict(params.arguments or {}))
        decision, forward = self.decide(call)
        if forward:
            return await call_next(ctx)
        return self.refusal(decision)

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
