"""The run matrix, the run ids, and the status projection.

Status is the one place B could accidentally start judging, so it is defined
as a projection of A's own transcript and nothing more.
"""

import json
import pathlib

from enrolment.agent.adversarial import RunSpec, enumerate_matrix, model_slug
from enrolment.agent.campaign import existing_status, run_status
from enrolment.agent.transcript import ResponseOutcome, Transcript, TurnRecord
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "attacks" / "scenarios"
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text()))
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
}
MODELS = ["llama3.1:8b", "llama3.2"]


def suite():
    return load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)


def test_a_model_slug_is_filesystem_safe():
    assert model_slug("llama3.1:8b") == "llama318b"
    assert model_slug("llama3.2") == "llama32"


def test_a_run_id_names_every_coordinate():
    spec = RunSpec(scenario_id="SC-07", model="llama3.1:8b", arm="on", repeat=3)
    assert spec.run_id == "SC-07-llama318b-on-r3"


def test_a_replacement_run_id_is_suffixed_not_reused():
    """Spec §8: run directories are single-use, so a replacement is a new one."""
    first = RunSpec(scenario_id="SC-07", model="llama3.2", arm="off", repeat=1)
    second = first.replacement()
    third = second.replacement()
    assert second.run_id == "SC-07-llama32-off-r1a"
    assert third.run_id == "SC-07-llama32-off-r1b"
    assert second.attempt == 1 and third.attempt == 2


def test_the_matrix_is_the_size_the_spec_states():
    """Spec §6: 160 on, 120 off, 280 total."""
    specs = enumerate_matrix(suite(), MODELS, repeats=5)
    assert len(specs) == 280
    assert len([s for s in specs if s.arm == "on"]) == 160
    assert len([s for s in specs if s.arm == "off"]) == 120


def test_the_undeliverable_four_get_no_off_arm():
    specs = enumerate_matrix(suite(), MODELS, repeats=5)
    off = {s.scenario_id for s in specs if s.arm == "off"}
    assert off.isdisjoint({"SC-01", "SC-03", "SC-05", "SC-08"})
    assert len(off) == 12


def test_every_run_id_is_unique():
    specs = enumerate_matrix(suite(), MODELS, repeats=5)
    assert len({s.run_id for s in specs}) == len(specs)


def test_the_matrix_completes_a_repeat_before_starting_the_next():
    """An interrupted campaign should leave balanced coverage, not five
    repeats of the first few scenarios and none of the rest."""
    specs = enumerate_matrix(suite(), MODELS, repeats=5)
    first_repeat = [s for s in specs[:56]]
    assert {s.repeat for s in first_repeat} == {1}
    assert specs[56].repeat == 2


def header_only(truncated=False, reason=None, turns=()):
    return Transcript(
        task_id="SC-07", model="fake", truncated=truncated,
        truncation_reason=reason, turns=list(turns),
    )


def test_a_clean_run_is_ok():
    assert run_status(header_only()) == "ok"


def test_a_bounded_run_is_truncated():
    assert run_status(header_only(True, "max_calls (12) reached")) == "truncated"


def test_a_provider_failure_outranks_the_truncated_flag():
    """A provider error also sets `truncated`, but the two mean different
    things: one is infrastructure, the other is the model reaching a bound.
    Only the first is ever replaced (spec §8)."""
    transcript = header_only(
        True, "provider error on user turn 0: TimeoutError: timed out",
        turns=[TurnRecord(
            user_turn=0, model_turn=0,
            outcome=ResponseOutcome.PROVIDER_ERROR, text="TimeoutError: timed out",
        )],
    )
    assert run_status(transcript) == "provider_error"


def test_a_missing_transcript_has_no_status(tmp_path):
    assert existing_status(tmp_path) is None


def test_an_unreadable_transcript_has_no_status(tmp_path):
    """Spec §9: a half-written transcript must read as "not run", so resume
    re-runs it rather than skipping a run that never finished being recorded."""
    (tmp_path / "transcript.jsonl").write_text("{not json", encoding="utf-8")
    assert existing_status(tmp_path) is None


def test_a_written_transcript_reports_its_status(tmp_path):
    header_only(True, "max_calls (12) reached").write_jsonl(tmp_path / "transcript.jsonl")
    assert existing_status(tmp_path) == "truncated"
