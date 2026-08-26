from policy_guard.engine import PolicyEngine
from policy_guard.models import Decision, TaskContext, ToolCall, Verdict


class FakePolicy:
    def __init__(self, name, verdict):
        self.name = name
        self.verdict = verdict
        self.called = False

    def check(self, call, context):
        self.called = True
        if self.verdict is None:
            return None
        return Decision(verdict=self.verdict, policy=self.name, reason="fake")


CALL = ToolCall(tool="anything")
CTX = TaskContext(task_id="t1")


def test_no_opinion_from_any_policy_allows():
    engine = PolicyEngine([FakePolicy("a", None)])
    assert engine.evaluate(CALL, CTX).verdict is Verdict.ALLOW


def test_deny_short_circuits():
    denier = FakePolicy("denier", Verdict.DENY)
    later = FakePolicy("later", Verdict.ALLOW)
    engine = PolicyEngine([denier, later])
    decision = engine.evaluate(CALL, CTX)
    assert decision.verdict is Verdict.DENY
    assert decision.policy == "denier"
    assert later.called is False


def test_require_approval_is_sticky_over_later_allow():
    engine = PolicyEngine(
        [FakePolicy("approval", Verdict.REQUIRE_APPROVAL), FakePolicy("allow", Verdict.ALLOW)]
    )
    decision = engine.evaluate(CALL, CTX)
    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.policy == "approval"


def test_later_deny_overrides_require_approval():
    engine = PolicyEngine(
        [FakePolicy("approval", Verdict.REQUIRE_APPROVAL), FakePolicy("denier", Verdict.DENY)]
    )
    assert engine.evaluate(CALL, CTX).verdict is Verdict.DENY
