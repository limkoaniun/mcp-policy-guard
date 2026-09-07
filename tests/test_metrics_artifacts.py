"""Reading a run, and deciding whether it may be scored at all.

Design §6: a metric whose denominator quietly shrank is the failure this
project keeps finding, so every exclusion is a named, counted reason rather
than a silent skip.
"""

import pathlib

from metrics_fixtures import call, decision, write_run

from enrolment.agent.campaign import IndexRecord
from enrolment.metrics.artifacts import (
    EXCLUSIONS,
    baseline_path,
    exclusion_for,
    read_run,
)

REPO = pathlib.Path(__file__).resolve().parents[1]


def record(**overrides) -> IndexRecord:
    base = {
        "run_id": "SC-01-fake-on-r1", "scenario_id": "SC-01", "model": "fake",
        "arm": "on", "repeat": 1, "status": "ok",
    }
    base.update(overrides)
    return IndexRecord(**base)


def test_a_complete_run_reads_every_artifact(tmp_path):
    write_run(
        tmp_path, "SC-01-fake-on-r1",
        calls=[call("enrol", code="32933")],
        enrolled={"S9000001": ["32933"]},
        erequests=[{"student_id": "S9000001", "text": "please"}],
        decisions=[decision("enrol")],
    )
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert artifacts.run_id == "SC-01-fake-on-r1"
    assert artifacts.transcript is not None
    assert artifacts.state["S9000001"]["enrolled"] == ["32933"]
    assert len(artifacts.erequests) == 1
    assert len(artifacts.decisions) == 1
    assert artifacts.complete is True


def test_an_absent_erequests_file_means_zero_not_broken(tmp_path):
    """The file is created on first append, so its absence is the ordinary
    case for a run that submitted nothing."""
    write_run(tmp_path, "SC-01-fake-on-r1", enrolled={"S9000001": []})
    assert read_run(tmp_path / "SC-01-fake-on-r1").erequests == []


def test_an_absent_state_file_is_none_not_an_empty_state(tmp_path):
    """`RunState.load` writes the baseline immediately, so the file's very
    existence means "this run got far enough to mutate state". An absent file
    is a run that crashed, and reading it as `{}` makes `_enrolment_changed`
    return False -- a crash scoring as a successful defence."""
    write_run(tmp_path, "SC-01-fake-on-r1")
    assert read_run(tmp_path / "SC-01-fake-on-r1").state is None


def test_a_truncated_state_file_is_none_not_an_empty_state(tmp_path):
    """`RunState.save` is a non-atomic `write_text`, so a half-written
    `state.json` is reachable."""
    run_dir = write_run(tmp_path, "SC-01-fake-on-r1", enrolled={"S9000001": []})
    (run_dir / "state.json").write_text('{"S9000001": {"enrol', encoding="utf-8")
    assert read_run(run_dir).state is None


def test_a_state_file_holding_an_empty_object_is_not_a_missing_one(tmp_path):
    """Parsed-empty is a fact about the run; absent is a fact about the
    harness. Collapsing the two is what this distinction exists to prevent."""
    run_dir = write_run(tmp_path, "SC-01-fake-on-r1")
    (run_dir / "state.json").write_text("{}", encoding="utf-8")
    assert read_run(run_dir).state == {}
    assert exclusion_for(record(), read_run(run_dir)) is None


def test_a_run_without_state_is_excluded(tmp_path):
    """Absence of evidence must not render as evidence of a defence."""
    write_run(tmp_path, "SC-01-fake-on-r1")
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert exclusion_for(record(), artifacts) == "no_state"


def test_an_unparseable_appended_jsonl_is_excluded_not_read_as_zero(tmp_path):
    """`decisions.jsonl` and `erequests.jsonl` are appended to as the run goes,
    so a partial trailing line is reachable. Returning `[]` would make an
    unreadable `erequests.jsonl` mean "zero eRequests" -- a false null on the
    exfiltration predicate, which is Critical 2 in a second guise."""
    run_dir = write_run(tmp_path, "SC-01-fake-on-r1", enrolled={"S9000001": []},
                        erequests=[{"student_id": "S9000001", "text": "hi"}])
    with (run_dir / "erequests.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"student_id": "S90000')
    artifacts = read_run(run_dir)
    assert artifacts.unreadable == ["erequests.jsonl"]
    assert exclusion_for(record(), artifacts) == "unreadable_jsonl"


def test_an_unreadable_transcript_reads_as_none(tmp_path):
    run_dir = write_run(tmp_path, "SC-01-fake-on-r1")
    (run_dir / "transcript.jsonl").write_text("{not json", encoding="utf-8")
    assert read_run(run_dir).transcript is None


def test_a_provider_error_run_is_excluded(tmp_path):
    write_run(tmp_path, "SC-01-fake-on-r1", provider_error=True)
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert exclusion_for(record(status="provider_error"), artifacts) == "provider_error"


def test_a_truncated_run_is_excluded(tmp_path):
    """A's §6: a run cut short by a bound must not count as a failed completion
    of turns that were never attempted."""
    write_run(tmp_path, "SC-01-fake-on-r1", truncated=True)
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert exclusion_for(record(status="truncated"), artifacts) == "truncated"


def test_a_run_without_a_complete_marker_is_excluded(tmp_path):
    write_run(tmp_path, "SC-01-fake-on-r1", complete=False)
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert exclusion_for(record(), artifacts) == "not_complete"


def test_a_run_with_no_transcript_is_excluded(tmp_path):
    write_run(tmp_path, "SC-01-fake-on-r1", transcript=False)
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert exclusion_for(record(), artifacts) == "no_transcript"


def test_a_clean_run_has_no_exclusion(tmp_path):
    write_run(tmp_path, "SC-01-fake-on-r1", enrolled={"S9000001": []})
    artifacts = read_run(tmp_path / "SC-01-fake-on-r1")
    assert exclusion_for(record(), artifacts) is None


def test_every_exclusion_reason_is_declared():
    """A reason invented at a call site is one nothing downstream knows to
    count -- the same defect ComplianceScenario's failure-mode validator
    exists to prevent."""
    assert set(EXCLUSIONS) == {
        "provider_error", "truncated", "not_complete", "no_transcript",
        "no_state", "unreadable_jsonl",
    }


def test_the_baseline_comes_from_the_pack_not_a_hardcoded_path():
    """Two packs point at different student files; comparing a run against the
    wrong one would silently misjudge every final state."""
    personas = baseline_path(REPO / "configs" / "enrolment-personas.yaml", REPO)
    students = baseline_path(REPO / "configs" / "enrolment.yaml", REPO)
    assert personas.name == "personas.json"
    assert students.name == "students.json"
    assert personas.exists() and students.exists()
