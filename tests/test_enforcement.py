import asyncio

from mcp.types import CallToolResult

from policy_guard.decisions import DecisionLog
from policy_guard.enforcement import EnforcementExtension
from policy_guard.engine import PolicyEngine
from policy_guard.models import Decision, TaskContext, ToolCall, Verdict
from policy_guard.policies.allowlist import AllowlistPolicy


def build(enforcement=True, allowed=("search_subjects",)):
    engine = PolicyEngine([AllowlistPolicy()])
    context = TaskContext(task_id="OI-001", allowed_tools=frozenset(allowed))
    return EnforcementExtension(engine, context, DecisionLog(None), enforcement=enforcement)


def test_a_declared_tool_is_allowed_and_forwarded():
    decision, forward = build().decide(ToolCall(tool="search_subjects"))
    assert decision.verdict is Verdict.ALLOW
    assert forward is True


def test_an_undeclared_tool_is_denied_and_not_forwarded():
    decision, forward = build().decide(ToolCall(tool="drop"))
    assert decision.verdict is Verdict.DENY
    assert forward is False


def test_the_deny_reason_names_the_tool():
    """The reason reaches the agent, so it has to be readable."""
    decision, _ = build().decide(ToolCall(tool="drop"))
    assert "drop" in decision.reason


def test_every_decision_is_logged():
    extension = build()
    extension.decide(ToolCall(tool="search_subjects"))
    extension.decide(ToolCall(tool="drop"))
    assert [r.verdict for r in extension.log.records] == ["allow", "deny"]
    assert [r.forwarded for r in extension.log.records] == [True, False]


def test_enforcement_off_forwards_but_still_records_the_verdict():
    """The with/without comparison is one code path, not two."""
    extension = build(enforcement=False)
    decision, forward = extension.decide(ToolCall(tool="drop"))
    assert decision.verdict is Verdict.DENY
    assert forward is True
    assert extension.log.records[0].forwarded is True
    assert extension.log.records[0].verdict == "deny"


def test_a_policy_that_raises_denies_the_call():
    """A layer that cannot decide must not thereby permit."""

    class Exploding:
        name = "exploding"

        def check(self, call, context):
            raise RuntimeError("policy is broken")

    engine = PolicyEngine([Exploding()])
    context = TaskContext(task_id="T", allowed_tools=frozenset({"anything"}))
    extension = EnforcementExtension(engine, context, DecisionLog(None))
    decision, forward = extension.decide(ToolCall(tool="anything"))
    assert decision.verdict is Verdict.DENY
    assert forward is False
    assert "policy is broken" in decision.reason


def test_require_approval_is_denied_in_deny_mode():
    class NeedsApproval:
        name = "taint"

        def check(self, call, context):
            from policy_guard.models import Decision

            return Decision(
                verdict=Verdict.REQUIRE_APPROVAL, policy="taint", reason="tainted context"
            )

    engine = PolicyEngine([NeedsApproval()])
    extension = EnforcementExtension(
        engine, TaskContext(task_id="T"), DecisionLog(None), approval_mode="deny"
    )
    _, forward = extension.decide(ToolCall(tool="enrol"))
    assert forward is False
    assert extension.log.records[0].approval_mode == "deny"


def test_require_approval_is_forwarded_in_approve_mode():
    class NeedsApproval:
        name = "taint"

        def check(self, call, context):
            from policy_guard.models import Decision

            return Decision(
                verdict=Verdict.REQUIRE_APPROVAL, policy="taint", reason="tainted context"
            )

    engine = PolicyEngine([NeedsApproval()])
    extension = EnforcementExtension(
        engine, TaskContext(task_id="T"), DecisionLog(None), approval_mode="approve"
    )
    _, forward = extension.decide(ToolCall(tool="enrol"))
    assert forward is True
    assert extension.log.records[0].approval_mode == "approve"


def test_a_refusal_survives_the_tools_call_result_validation():
    """A bare dict is rejected on the way out and the reason is lost with it."""
    from mcp_types import methods

    decision = Decision(verdict=Verdict.DENY, policy="allowlist", reason="'drop' is not allowed")
    payload = EnforcementExtension.refusal(decision)
    dumped = (
        payload.model_dump(by_alias=True, mode="json", exclude_none=True)
        if hasattr(payload, "model_dump")
        else payload
    )
    serialised = methods.serialize_server_result("tools/call", "2025-06-18", dumped)
    assert "'drop' is not allowed" in str(serialised)
    # The reason surviving is not enough on its own: `isError` is what makes an
    # agent treat the block as a refusal to relay rather than as the tool's
    # ordinary result. Assert on the wire alias, because that is the spelling
    # that crosses the connection.
    assert serialised["isError"] is True


def test_intercept_tool_call_short_circuits_on_deny_and_forwards_on_allow():
    """The upstream must never see a denied call, and an allowed call must reach it."""

    class Params:
        def __init__(self, name):
            self.name = name
            self.arguments = {}

    recorded_ctx = []

    async def call_next(ctx):
        recorded_ctx.append(ctx)
        return "forwarded-result"

    extension = build()
    sentinel_ctx = object()

    deny_result = asyncio.run(
        extension.intercept_tool_call(Params("drop"), sentinel_ctx, call_next)
    )
    assert recorded_ctx == []
    assert isinstance(deny_result, CallToolResult)
    assert deny_result.is_error is True

    allow_result = asyncio.run(
        extension.intercept_tool_call(Params("search_subjects"), sentinel_ctx, call_next)
    )
    assert recorded_ctx == [sentinel_ctx]
    assert allow_result == "forwarded-result"
