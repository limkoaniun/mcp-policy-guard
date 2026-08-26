from policy_guard.engine import PolicyEngine
from policy_guard.models import TaskContext, ToolCall, Verdict
from policy_guard.policies.allowlist import AllowlistPolicy


def make_engine():
    return PolicyEngine(policies=[AllowlistPolicy()])


def ctx(*tools):
    return TaskContext(task_id="t1", allowed_tools=frozenset(tools))


def test_declared_tool_is_allowed():
    engine = make_engine()
    decision = engine.evaluate(ToolCall(tool="search_subjects"), ctx("search_subjects"))
    assert decision.verdict is Verdict.ALLOW
    assert decision.policy == "allowlist"


def test_undeclared_tool_is_denied():
    engine = make_engine()
    decision = engine.evaluate(ToolCall(tool="drop"), ctx("search_subjects"))
    assert decision.verdict is Verdict.DENY
    assert "drop" in decision.reason


def test_empty_allowlist_denies_by_default():
    engine = make_engine()
    decision = engine.evaluate(ToolCall(tool="search_subjects"), ctx())
    assert decision.verdict is Verdict.DENY


def test_audit_log_records_every_decision():
    engine = make_engine()
    engine.evaluate(ToolCall(tool="search_subjects"), ctx("search_subjects"))
    engine.evaluate(ToolCall(tool="drop"), ctx("search_subjects"))
    assert len(engine.audit_log) == 2
    verdicts = [d.verdict for _, d in engine.audit_log]
    assert verdicts == [Verdict.ALLOW, Verdict.DENY]
