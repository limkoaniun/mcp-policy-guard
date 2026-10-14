"""The demo set: three realistic documents for a live demonstration, each a
re-telling of a measured scenario (A-01 with a different planted note, A-13,
A-04) with fuller, more believable text. Kept outside `attacks/scenarios/`
so the suite of record and every campaign number stay exactly as measured."""

import json
import pathlib

import pytest

from enrolment.scenario_runner import run_scenario
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
DEMO = REPO / "attacks" / "scenarios-demo"
SCENARIOS = REPO / "attacks" / "scenarios"
REPLY = REPO / "attacks" / "scenarios-reply"
PACK = REPO / "configs" / "enrolment-personas.yaml"
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text()))
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
    "get_attachment",
}


def load(root):
    return load_all(root, personas=PERSONAS, known_tools=KNOWN_TOOLS)


def by_id(root):
    return {s.id: s for s in load(root)}


def test_the_demo_set_is_three_attacks_with_twins():
    demo = by_id(DEMO)
    assert sorted(demo) == ["DEMO-01", "DEMO-01b", "DEMO-02", "DEMO-02b", "DEMO-03", "DEMO-03b"]
    for scenario_id in ("DEMO-01", "DEMO-02", "DEMO-03"):
        scenario, twin = demo[scenario_id], demo[f"{scenario_id}b"]
        assert twin.persona == scenario.persona
        assert twin.erequest_action == scenario.erequest_action
        assert set(twin.task.allowed_tools) == set(scenario.task.allowed_tools)
        assert twin.task.description == scenario.task.description
        assert twin.attachments == scenario.attachments
        assert twin.payload is None and twin.attack_class is None
        assert scenario.payload.location == "attachment"
        assert scenario.payload.obeyed_if
        assert scenario.deliverable


def test_the_demo_set_mirrors_the_measured_scenarios():
    demo, record = by_id(DEMO), by_id(SCENARIOS)
    assert demo["DEMO-01"].attack_class == "rule-breaking-action"
    assert demo["DEMO-01"].persona == record["A-01"].persona
    assert "human evaluation" in demo["DEMO-01"].payload.text
    assert demo["DEMO-01"].payload.obeyed_if == ["human evaluation"]
    # DEMO-02 is the cohort variant of the reply-channel class: the note names
    # Omar's whole study group, and principal binding denies each read.
    assert demo["DEMO-02"].attack_class == "exfiltration-reply"
    assert demo["DEMO-02"].persona == "S9000002"
    for other in ("S9000001", "S9000003", "S9000004"):
        assert other in demo["DEMO-02"].payload.text, other
    assert demo["DEMO-02"].payload.obeyed_if == ["32933", "420108"]
    assert demo["DEMO-03"].attack_class == "exfiltration"
    assert demo["DEMO-03"].payload.text.strip() == record["A-04"].payload.text.strip()
    assert demo["DEMO-03"].payload.obeyed_if == record["A-04"].payload.obeyed_if


def test_demo_document_names_are_distinct_from_the_measured_ones():
    """`match_known` compares an upload against every known document; a demo
    document that reused a measured one's file name would be ambiguous."""
    names = {a.name for s in load(DEMO) for a in s.attachments}
    measured = {a.name for root in (SCENARIOS, REPLY) for s in load(root) for a in s.attachments}
    assert not names & measured, names & measured


def test_demo_texts_are_latin_1():
    """The PDF builder's core font is Latin-1; a curly quote would make it raise."""
    for scenario in load(DEMO):
        for attachment in scenario.attachments:
            attachment.text.encode("latin-1")
        if scenario.payload:
            scenario.payload.text.encode("latin-1")


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", load(DEMO) if DEMO.is_dir() else [], ids=lambda s: s.id)
async def test_every_demo_scenario_replays_as_expected(scenario, tmp_path):
    result = await run_scenario(scenario, run_dir=tmp_path / "run", pack_path=PACK, repo_root=REPO)
    assert result.passed, result.failures
