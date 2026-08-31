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


def test_the_logged_tainted_flag_is_read_from_the_context():
    """Driven through `decide()`, which is where the value is actually read.

    `tests/test_decisions.py` passes `tainted=True` straight into `log.record`,
    so it never exercised the line that supplies it. That line was
    `getattr(self.context, "tainted", False)` against a `TaskContext` with no
    such field, which made `tainted` a hard-coded `false` wearing the clothes
    of a measurement: every record in every `decisions.jsonl` said `false`, a
    harness computing a tainted-call rate got 0%, and nothing raised.

    Reading it as a plain attribute is the point of the fix -- a later
    field-name typo then fails loudly instead of silently reporting 0% forever.
    """
    default = build()
    default.decide(ToolCall(tool="search_subjects"))
    assert default.log.records[0].tainted is False

    engine = PolicyEngine([AllowlistPolicy()])
    context = TaskContext(
        task_id="OI-001", allowed_tools=frozenset({"search_subjects"}), tainted=True
    )
    tainted = EnforcementExtension(engine, context, DecisionLog(None))
    tainted.decide(ToolCall(tool="search_subjects"))
    assert tainted.log.records[0].tainted is True


from policy_guard.policies.arg_validation import ArgumentValidationPolicy


def ctx(**kwargs):
    defaults = {"task_id": "T1", "allowed_tools": frozenset({"enrol"})}
    defaults.update(kwargs)
    return TaskContext(**defaults)


def test_arg_validation_denies_a_call_naming_another_principal():
    policy = ArgumentValidationPolicy(principal_argument="student_id")
    call = ToolCall(tool="enrol", arguments={"student_id": "S9000002", "code": "42027"})

    decision = policy.check(call, ctx(principal_id="S9000001"))

    assert decision.verdict is Verdict.DENY
    assert "S9000002" in decision.reason
    assert "S9000001" in decision.reason


def test_arg_validation_allows_the_bound_principal():
    policy = ArgumentValidationPolicy(principal_argument="student_id")
    call = ToolCall(tool="enrol", arguments={"student_id": "S9000001", "code": "42027"})

    assert policy.check(call, ctx(principal_id="S9000001")) is None


def test_arg_validation_denies_when_the_principal_argument_is_missing():
    """A call that omits the principal cannot be verified, so it is denied."""
    policy = ArgumentValidationPolicy(principal_argument="student_id")

    decision = policy.check(ToolCall(tool="enrol", arguments={"code": "42027"}),
                            ctx(principal_id="S9000001"))

    assert decision.verdict is Verdict.DENY


def test_arg_validation_has_no_opinion_when_no_principal_is_bound():
    policy = ArgumentValidationPolicy(principal_argument="student_id")
    call = ToolCall(tool="enrol", arguments={"student_id": "S9000002"})

    assert policy.check(call, ctx()) is None


def test_build_context_carries_the_principal():
    from policy_guard.config import TaskDefinition, build_context

    task = TaskDefinition(task_id="T1", allowed_tools=["enrol"], principal_id="S9000001")

    assert build_context(task).principal_id == "S9000001"
