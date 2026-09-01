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
}


def suite():
    return load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)


def test_every_scenario_file_loads():
    assert len(suite()) == 16


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


def test_no_benign_scenario_carries_an_attack_class_or_payload():
    for scenario in suite():
        if not scenario.injected:
            assert scenario.payload is None


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", suite(), ids=lambda s: s.id)
async def test_every_scenario_replays_as_expected(scenario, tmp_path):
    result = await run_scenario(
        scenario, run_dir=tmp_path / "run", pack_path=PACK, repo_root=REPO
    )
    assert result.passed, result.failures
