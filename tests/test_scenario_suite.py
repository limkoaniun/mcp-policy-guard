"""Suite-level properties: every file loads, the classes are covered, and the
coverage report names what `deliverable: false` excludes."""

import json
import pathlib

import pytest

from enrolment.scenario_runner import run_scenario
from enrolment.scenarios_spec import ATTACK_CLASSES, load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "attacks" / "scenarios"
PACK = REPO / "configs" / "enrolment-personas.yaml"
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text()))
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
    "get_attachment",
}


def suite():
    return load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)


def test_every_scenario_file_loads():
    assert len(suite()) == 24


def test_every_attack_class_is_instantiated():
    covered = {s.attack_class for s in suite() if s.injected}
    assert covered == ATTACK_CLASSES


def test_each_injected_scenario_has_a_benign_twin():
    injected = {s.id for s in suite() if s.injected}
    benign = {s.id for s in suite() if not s.injected}
    assert {f"{i}b" for i in injected} == benign


def test_the_deliverable_split_is_what_the_spec_states():
    """Spec §7: half the injected suite has no enforcement-off arm."""
    injected = [s for s in suite() if s.injected]
    undeliverable = sorted(s.id for s in injected if not s.deliverable)
    assert undeliverable == ["SC-01", "SC-03", "SC-05", "SC-08"]
    assert sorted(s.attack_class for s in injected if not s.deliverable) == [
        "poisoned-description", "poisoned-description",
        "tool-shadowing", "tool-shadowing",
    ]


def test_each_benign_twin_matches_its_injected_counterpart():
    """The benign/injected pairing is only evidence of enforcement if the two
    runs differ solely in whether a payload is present. Compare everything
    else that should be identical; `replay` is deliberately excluded because
    the injected twin replays the calls an attack would induce while the
    benign twin replays the legitimate ones."""
    by_id = {s.id: s for s in suite()}
    for scenario in by_id.values():
        if not scenario.injected:
            continue
        twin = by_id[f"{scenario.id}b"]
        assert twin.persona == scenario.persona
        assert twin.erequest_action == scenario.erequest_action
        assert set(twin.task.allowed_tools) == set(scenario.task.allowed_tools)
        assert twin.attack_class is None
        assert twin.payload is None
        assert twin.attachments == scenario.attachments
        assert twin.task.description == scenario.task.description


def test_every_upload_borne_scenario_delivers_through_an_attachment():
    """The new classes exist to put the payload where a student would: in a
    document they uploaded. A scenario in one of them that delivered any
    other way would be measuring the old channel under a new name."""
    for scenario in suite():
        if scenario.attack_class in {"rule-breaking-action", "out-of-task-action",
                                     "cross-principal-action"} or scenario.id == "A-04":
            assert scenario.payload.location == "attachment", scenario.id
            assert scenario.deliverable, scenario.id


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", suite(), ids=lambda s: s.id)
async def test_every_scenario_replays_as_expected(scenario, tmp_path):
    result = await run_scenario(
        scenario, run_dir=tmp_path / "run", pack_path=PACK, repo_root=REPO
    )
    assert result.passed, result.failures
