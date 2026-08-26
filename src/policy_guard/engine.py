"""Policy engine: evaluates a proposed tool call against the active policy set.

Each policy module exposes `check(call, context) -> Decision | None`:
- `None` means the policy has no opinion on this call.
- The engine short-circuits on the first DENY.
- REQUIRE_APPROVAL is sticky: once any policy requires approval, the call
  requires approval unless a later policy denies it outright.

The evaluation harness computes attack success / rule violation metrics from
the JSONL decision log (`runs/<id>/decisions.jsonl`, written by
`decisions.py`), never from here.

`audit_log` below is an in-process convenience for tests and for inspecting an
engine in a REPL. It is deliberately *not* the harness's source, and it is not
the same set of records: when a policy raises, `enforcement.py` synthesises the
DENY outside the engine, so `evaluate` is never reached and that call leaves no
trace here at all. Scoring from `audit_log` would silently miss exactly the
calls a broken policy denied.
"""

from collections.abc import Sequence
from typing import Protocol

from .models import Decision, TaskContext, ToolCall, Verdict


class Policy(Protocol):
    name: str

    def check(self, call: ToolCall, context: TaskContext) -> Decision | None: ...


class PolicyEngine:
    """Combines individual policies into a single decision per tool call."""

    def __init__(self, policies: Sequence[Policy]):
        self.policies = list(policies)
        self.audit_log: list[tuple[ToolCall, Decision]] = []

    def evaluate(self, call: ToolCall, context: TaskContext) -> Decision:
        final = Decision(
            verdict=Verdict.ALLOW,
            policy="engine",
            reason="no policy objected",
        )
        for policy in self.policies:
            decision = policy.check(call, context)
            if decision is None:
                continue
            if decision.verdict is Verdict.DENY:
                self.audit_log.append((call, decision))
                return decision
            if decision.verdict is Verdict.REQUIRE_APPROVAL:
                final = decision
            elif final.verdict is Verdict.ALLOW:
                # an explicit ALLOW from a policy carries its reason,
                # but never overrides a sticky REQUIRE_APPROVAL
                final = decision
        self.audit_log.append((call, final))
        return final
