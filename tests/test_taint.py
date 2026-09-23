"""Gate 4: a side-effecting call proposed after an untrusted read is held.

The policy reasons about provenance and nothing else. It does not read the
call's arguments and it does not read the document; deciding what a document
"really" asked for is the judgement this layer exists to keep out of the loop.
"""

from policy_guard.engine import PolicyEngine
from policy_guard.models import Decision, TaskContext, ToolCall, Verdict
from policy_guard.policies.allowlist import AllowlistPolicy
from policy_guard.policies.arg_validation import ArgumentValidationPolicy
from policy_guard.policies.taint import TaintPolicy

ACTIONS = ("enrol", "drop", "submit_erequest")


def ctx(**kwargs):
    defaults = {"task_id": "T", "allowed_tools": frozenset({*ACTIONS, "get_attachment"})}
    defaults.update(kwargs)
    return TaskContext(**defaults)


def test_an_untainted_context_gets_no_opinion():
    policy = TaintPolicy(ACTIONS)
    assert policy.check(ToolCall(tool="enrol"), ctx()) is None


def test_a_tainted_side_effecting_call_requires_approval_and_names_the_source():
    policy = TaintPolicy(ACTIONS)
    context = ctx(tainted=True, taint_sources=["get_attachment"])
    decision = policy.check(ToolCall(tool="enrol"), context)
    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.policy == "taint"
    assert "get_attachment" in decision.reason
    assert "enrol" in decision.reason
    assert "confirmation" in decision.reason


def test_a_tainted_read_only_call_gets_no_opinion():
    policy = TaintPolicy(ACTIONS)
    context = ctx(tainted=True, taint_sources=["get_attachment"])
    assert policy.check(ToolCall(tool="get_student_record"), context) is None


def test_the_policy_never_reads_the_arguments():
    """Same verdict whatever the call carries: provenance is the only input."""
    policy = TaintPolicy(ACTIONS)
    context = ctx(tainted=True, taint_sources=["get_attachment"])
    bland = policy.check(ToolCall(tool="submit_erequest", arguments={"text": "hi"}), context)
    loud = policy.check(
        ToolCall(tool="submit_erequest", arguments={"text": "S9000001 completed 32548"}),
        context,
    )
    assert bland.verdict is loud.verdict is Verdict.REQUIRE_APPROVAL
    assert bland.reason == loud.reason


def test_a_rule_denial_beats_taint_in_the_engine():
    """A rule-breaking action is refused AS a rule violation even when tainted."""
    engine = PolicyEngine([
        AllowlistPolicy(),
        ArgumentValidationPolicy(validators={"enrol": lambda args: "32513 requires: 32130"}),
        TaintPolicy(ACTIONS),
    ])
    context = ctx(tainted=True, taint_sources=["get_attachment"])
    decision = engine.evaluate(ToolCall(tool="enrol", arguments={"code": "32513"}), context)
    assert decision.verdict is Verdict.DENY
    assert decision.policy == "arg_validation"


def test_an_allowlist_denial_beats_taint_in_the_engine():
    engine = PolicyEngine([AllowlistPolicy(), TaintPolicy(ACTIONS)])
    context = ctx(allowed_tools=frozenset({"get_attachment"}), tainted=True,
                  taint_sources=["get_attachment"])
    decision = engine.evaluate(ToolCall(tool="drop"), context)
    assert decision.verdict is Verdict.DENY
    assert decision.policy == "allowlist"


def test_a_permitted_tainted_action_is_held_by_the_engine():
    """The only gate that can stop an action the rules permit."""
    engine = PolicyEngine([AllowlistPolicy(), ArgumentValidationPolicy(), TaintPolicy(ACTIONS)])
    context = ctx(tainted=True, taint_sources=["get_attachment"])
    decision = engine.evaluate(ToolCall(tool="drop", arguments={"code": "32541"}), context)
    assert decision.verdict is Verdict.REQUIRE_APPROVAL
    assert decision.policy == "taint"


def test_taint_sources_default_to_empty():
    context = TaskContext(task_id="T")
    assert context.taint_sources == []
    assert context.tainted is False
