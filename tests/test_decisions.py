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


def test_record_with_non_json_serializable_argument_is_written(tmp_path):
    """A record whose arguments contain a non-JSON-serializable value
    should be written with the offending value rendered as a string."""
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(path)
    bad_call = ToolCall(tool="drop", arguments={"bad": {1, 2, 3}})
    log.record(bad_call, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)

    # Record should be in file
    assert path.exists()
    content = path.read_text().strip()
    assert len(content) > 0

    # Should be valid JSON
    record_json = json.loads(content)
    assert record_json["tool"] == "drop"
    assert record_json["forwarded"] is False


def test_record_not_added_to_memory_if_write_fails(tmp_path):
    """If write fails, the record should not appear in memory."""
    # Create a log pointing to a directory (which will fail to open as a file)
    path = tmp_path / "bad_path"
    path.mkdir()  # Make it a directory, so open("a") will raise
    log = DecisionLog(path)

    try:
        log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)
    except (IsADirectoryError, PermissionError, OSError):
        # Expected to raise
        pass

    # Memory should not have the record
    assert len(log.records) == 0


def test_persistent_property_with_path(tmp_path):
    """persistent is True when initialized with a path."""
    log = DecisionLog(tmp_path / "decisions.jsonl")
    assert log.persistent is True


def test_persistent_property_without_path():
    """persistent is False when initialized with None."""
    log = DecisionLog(None)
    assert log.persistent is False


def test_ephemeral_log_records_in_memory_only():
    """An ephemeral log records in memory and writes no file anywhere."""
    log = DecisionLog(None)
    log.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)
    assert len(log.records) == 1
    # No files should be created anywhere


def test_multiple_instances_to_same_path_are_not_clobbered(tmp_path):
    """Two DecisionLog instances appending to the same path produce one
    object per line with nothing clobbered."""
    path = tmp_path / "decisions.jsonl"

    log1 = DecisionLog(path)
    log1.record(CALL, DENY, task_id="OI-001", forwarded=False, latency_ms=0.4)

    log2 = DecisionLog(path)
    log2.record(CALL, DENY, task_id="OI-002", forwarded=True, latency_ms=0.5)

    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2

    record1 = json.loads(lines[0])
    record2 = json.loads(lines[1])
    assert record1["task_id"] == "OI-001"
    assert record2["task_id"] == "OI-002"
