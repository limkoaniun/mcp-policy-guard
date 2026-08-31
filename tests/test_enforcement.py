import json
from pathlib import Path

from policy_guard.decisions import DecisionLog
from policy_guard.enforcement import EnforcementExtension
from policy_guard.engine import PolicyEngine
from policy_guard.models import Decision, TaskContext, ToolCall, Verdict
from policy_guard.policies.allowlist import AllowlistPolicy
from policy_guard.policies.arg_validation import ArgumentValidationPolicy

DATA = Path(__file__).resolve().parents[1] / "data"


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


def test_arg_validation_allows_an_exempt_tool_called_without_the_principal():
    """A tool the pack declares exempt has no principal to bind to at all.

    `search_subjects(query)` and `get_subject_details(code)` are the real
    example: neither takes a `student_id`, so denying them for lacking one
    would deny every call to the entire catalogue-search surface -- which is
    exactly what happened before `exempt_tools` existed, and it made the
    shipped enrolment pack unable to read the catalogue at all once
    `principal_id` became effectively mandatory.
    """
    policy = ArgumentValidationPolicy(
        principal_argument="student_id", exempt_tools=["search_subjects"]
    )
    call = ToolCall(tool="search_subjects", arguments={"query": "network"})

    assert policy.check(call, ctx(principal_id="S9000001")) is None


def test_arg_validation_still_denies_an_exempt_tool_given_the_wrong_principal():
    """Exemption covers *omitting* the principal, never naming the wrong one.

    A call that manages to supply a mismatched `student_id` on an otherwise
    exempt tool must still be denied outright -- exemption is not a way to
    launder an impersonating call through an argument the tool happens not to
    require.
    """
    policy = ArgumentValidationPolicy(
        principal_argument="student_id", exempt_tools=["search_subjects"]
    )
    call = ToolCall(
        tool="search_subjects", arguments={"query": "network", "student_id": "S9000002"}
    )

    decision = policy.check(call, ctx(principal_id="S9000001"))

    assert decision.verdict is Verdict.DENY
    assert "S9000002" in decision.reason
    assert "S9000001" in decision.reason


def test_arg_validation_has_no_opinion_when_no_principal_is_bound():
    policy = ArgumentValidationPolicy(principal_argument="student_id")
    call = ToolCall(tool="enrol", arguments={"student_id": "S9000002"})

    assert policy.check(call, ctx()) is None


def test_build_context_carries_the_principal():
    from policy_guard.config import TaskDefinition, build_context

    task = TaskDefinition(task_id="T1", allowed_tools=["enrol"], principal_id="S9000001")

    assert build_context(task).principal_id == "S9000001"


def test_enrolment_validators_deny_an_unmet_requisite():
    from enrolment.policy_pack import build_validators

    validators = build_validators(DATA, DATA / "personas.json")
    reason = validators["enrol"]({"student_id": "S9000002", "code": "42027"})

    assert reason is not None
    assert "32547" in reason


def test_enrolment_validators_deny_an_anti_requisite_block():
    from enrolment.policy_pack import build_validators

    validators = build_validators(DATA, DATA / "personas.json")
    reason = validators["enrol"]({"student_id": "S9000003", "code": "32548"})

    assert reason is not None
    assert "420108" in reason


def test_enrolment_validators_deny_exceeding_the_credit_cap():
    """32520 publishes no requisites at all, so only the cap check can fire.

    The brief's original version of this test used 42035, which publishes a
    requisite on 32548 -- a subject S9000004 has only *enrolled* in, not
    completed -- so the unmet-requisite reason fired first and the cap check
    was never exercised. 32520 has no `requisite_rule` at all, and
    S9000004 is already carrying exactly the 24cp cap, so this isolates the
    cap check the test is meant to cover.
    """
    from enrolment.policy_pack import build_validators

    validators = build_validators(DATA, DATA / "personas.json")
    reason = validators["enrol"]({"student_id": "S9000004", "code": "32520"})

    assert reason is not None
    assert "24" in reason


def test_enrolment_validators_allow_a_satisfied_enrolment():
    from enrolment.policy_pack import build_validators

    validators = build_validators(DATA, DATA / "personas.json")

    assert validators["enrol"]({"student_id": "S9000001", "code": "32009"}) is None


def _write_students(path, student_id, enrolled, cap=24):
    path.write_text(
        json.dumps(
            {
                student_id: {
                    "student_id": student_id,
                    "course": "C04296",
                    "completed": [],
                    "enrolled": list(enrolled),
                    "credit_point_cap_per_session": cap,
                }
            }
        ),
        encoding="utf-8",
    )


def test_the_credit_cap_binds_against_live_run_state_across_repeated_calls(tmp_path):
    """The test that matters most in this branch.

    `build_validators` used to load `students_path` once and close over it.
    The upstream mutates a run's real enrolment in `<run_dir>/state.json` via
    `enrolment.state.RunState`; the closure never re-read it, so the
    credit-point cap was checked against the same frozen baseline on every
    call. A student starting 18cp under a 24cp cap was ALLOWED five
    consecutive requisite-free enrolments and reached 48cp -- double the cap,
    every call permitted, because the cap never bound again after the first.

    This drives the `enrol` validator against a real `RunState` on the same
    run directory, actually applying every call it allows, and asserts the
    cap holds across the whole sequence -- not only on the first call --
    using requisite-free 6cp subjects throughout so the cap is the only gate
    that could possibly fire (the same trap Ruling A already caught once in
    this task: a requisite denial would pass this test without the cap ever
    being exercised).
    """
    from enrolment.policy_pack import build_validators
    from enrolment.state import RunState

    student_id = "S9100001"
    cap = 24
    # 3 requisite-free 6cp subjects = 18cp baseline, 6cp under the cap.
    baseline = ["42913", "42850", "32567"]
    # Requisite-free 6cp subjects, disjoint from the baseline.
    candidates = ["32549", "32569", "32560", "32603"]

    students_path = tmp_path / "students.json"
    _write_students(students_path, student_id, baseline, cap)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RunState.load(students_path, run_dir=run_dir)

    validators = build_validators(DATA, students_path, run_dir=run_dir)
    enrol = validators["enrol"]

    sequence = []
    for code in candidates:
        reason = enrol({"student_id": student_id, "code": code})
        allowed = reason is None
        if allowed:
            state.enrol(student_id, code)
        sequence.append((code, allowed))

    # Exactly one more 6cp subject fits under the 24cp cap (18 + 6 = 24, not
    # over); every subsequent attempt must be denied, not just the first.
    assert sequence == [
        ("32549", True),
        ("32569", False),
        ("32560", False),
        ("32603", False),
    ], sequence

    catalogue = json.loads((DATA / "catalogue.json").read_text())
    final = json.loads((run_dir / "state.json").read_text())
    final_enrolled = final[student_id]["enrolled"]
    final_load = sum(catalogue[c]["credit_points"] for c in final_enrolled)
    assert final_load == 24
    assert final_load <= cap


def test_enrolment_validators_deny_an_unknown_enrolled_code_rather_than_price_it_at_zero(
    tmp_path,
):
    """An enrolled code missing from the catalogue must not silently count as 0cp.

    `catalogue[c].credit_points for c in student.enrolled if c in catalogue`
    would drop an unrecognised code from the sum entirely -- undercounting
    the session load the cap is checked against, which is the fail-open
    direction and contradicts "never permit what could not be verified".
    """
    from enrolment.policy_pack import build_validators

    student_id = "S9100002"
    students_path = tmp_path / "students.json"
    _write_students(students_path, student_id, ["NOTACODE"])

    validators = build_validators(DATA, students_path)
    reason = validators["enrol"]({"student_id": student_id, "code": "32549"})

    assert reason is not None
    assert "NOTACODE" in reason
