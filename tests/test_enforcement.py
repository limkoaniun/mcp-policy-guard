from policy_guard.decisions import DecisionLog
from policy_guard.enforcement import EnforcementExtension
from policy_guard.engine import PolicyEngine
from policy_guard.models import TaskContext, ToolCall, Verdict
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
