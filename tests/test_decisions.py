import json

from policy_guard.decisions import DecisionLog
from policy_guard.models import Decision, ToolCall, Verdict

CALL = ToolCall(tool="drop", arguments={"student_id": "S1", "code": "31257"})
DENY = Decision(verdict=Verdict.DENY, policy="allowlist", reason="'drop' is not allowed")


def test_records_are_kept_in_memory(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)
    assert len(log.records) == 1
    assert log.records[0].verdict == "deny"


def test_one_json_object_per_line(tmp_path):
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(path)
    log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)
    log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.5)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["tool"] == "drop"


def test_the_record_carries_what_the_harness_needs(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    record = log.record(
        CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4, tainted=True
    )
    assert record.task_id == "OI-001"
    assert record.policy == "allowlist"
    assert record.reason == "'drop' is not allowed"
    assert record.arguments == {"student_id": "S1", "code": "31257"}
    assert record.forwarded is False
    assert record.tainted is True
    assert record.ts


def test_a_denied_call_is_recorded_as_not_forwarded(tmp_path):
    """The audit trail must show that the upstream never saw it."""
    log = DecisionLog(tmp_path / "decisions.jsonl")
    log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)
    assert json.loads((tmp_path / "decisions.jsonl").read_text())["forwarded"] is False


def test_a_log_without_a_path_still_records_in_memory():
    log = DecisionLog(None)
    log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)
    assert len(log.records) == 1


def test_timestamps_are_injectable_for_determinism(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    record = log.record(
        CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4, now="2026-08-26T00:00:00"
    )
    assert record.ts == "2026-08-26T00:00:00"
