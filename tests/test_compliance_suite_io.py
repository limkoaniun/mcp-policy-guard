"""The compliance suite must survive a round trip to disk.

`write_jsonl` has existed since the suite was generated; nothing read it back,
so the file's shape was only ever asserted by the generator that wrote it.
Subsystem C reads it on every campaign.
"""

import pathlib

import pytest

from enrolment.scenarios import ComplianceScenario, read_jsonl, write_jsonl

REPO = pathlib.Path(__file__).resolve().parents[1]
SUITE = REPO / "data" / "compliance_scenarios.jsonl"


def scenario(**overrides):
    base = {
        "scenario_id": "S1000001-32933",
        "student_id": "S1000001",
        "subject_code": "32933",
        "failure_mode": "unmet_academic",
        "rule_depth": 3,
        "expected_satisfied": False,
        "explanation": "32933 requires: 32144 Technology Research Preparation.",
    }
    base.update(overrides)
    return ComplianceScenario(**base)


def test_the_suite_round_trips(tmp_path):
    written = [scenario(), scenario(scenario_id="S1000002-32040", subject_code="32040")]
    path = tmp_path / "suite.jsonl"
    write_jsonl(path, written)
    assert read_jsonl(path) == written


def test_reading_the_real_suite_gives_every_scenario():
    """The committed suite is the input to every compliance campaign. If its
    shape drifts from the model, every campaign is built on a misreading."""
    scenarios = read_jsonl(SUITE)
    assert len(scenarios) == 4650
    assert all(isinstance(s, ComplianceScenario) for s in scenarios)


def test_a_scenario_with_an_unknown_failure_mode_is_refused(tmp_path):
    """`ComplianceScenario` already refuses an unknown mode at construction --
    a mode nothing downstream knows to look for is a population quietly
    missing from every summary that reads as complete. The reader must not
    bypass that validator."""
    path = tmp_path / "bad.jsonl"
    path.write_text(
        '{"scenario_id": "x", "student_id": "S1", "subject_code": "1", '
        '"failure_mode": "invented", "rule_depth": 0, "expected_satisfied": true, '
        '"explanation": ""}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown failure mode"):
        read_jsonl(path)


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "suite.jsonl"
    write_jsonl(path, [scenario()])
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert len(read_jsonl(path)) == 1
