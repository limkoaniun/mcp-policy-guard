"""Run-directory setup, shared by the replay arm and the model-driven arm.

Two arms building run directories differently is the divergence shape this
project has already had to close once. These tests pin the shared behaviour.
"""

import json
import pathlib

import yaml

from enrolment.run_setup import arms_for, off_arm_allowed, write_overlay, write_task
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "attacks" / "scenarios"
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text()))
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
}


def by_id():
    return {s.id: s for s in load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)}


def test_an_injected_scenario_that_cannot_deliver_has_no_off_arm():
    """Spec §6: SC-01, SC-03, SC-05 and SC-08 are `deliverable: false`, so an
    off arm would report "the attack failed" when none was mounted."""
    scenarios = by_id()
    for sid in ("SC-01", "SC-03", "SC-05", "SC-08"):
        assert not off_arm_allowed(scenarios[sid]), sid
        assert arms_for(scenarios[sid]) == ["on"], sid


def test_a_deliverable_injected_scenario_has_both_arms():
    scenarios = by_id()
    for sid in ("SC-02", "SC-04", "SC-06", "SC-07"):
        assert off_arm_allowed(scenarios[sid]), sid
        assert arms_for(scenarios[sid]) == ["on", "off"], sid


def test_every_benign_scenario_has_both_arms():
    """A benign twin mounts no attack, so nothing can fail to be delivered.
    Its off arm is the utility control the completion metric needs."""
    for scenario in by_id().values():
        if not scenario.injected:
            assert off_arm_allowed(scenario), scenario.id
            assert arms_for(scenario) == ["on", "off"], scenario.id


def test_write_task_records_the_principal_and_the_allowlist(tmp_path):
    scenario = by_id()["SC-07"]
    path = write_task(
        tmp_path,
        task_id=scenario.id,
        description=scenario.task.description,
        allowed_tools=scenario.task.allowed_tools,
        principal_id=scenario.persona,
    )
    written = yaml.safe_load(path.read_text())
    assert written["task_id"] == "SC-07"
    assert written["principal_id"] == scenario.persona
    assert written["allowed_tools"] == list(scenario.task.allowed_tools)
    assert "overlay" not in written


def test_write_overlay_delivers_a_deliverable_payload(tmp_path):
    scenario = by_id()["SC-07"]
    path = write_overlay(scenario, tmp_path)
    assert path is not None
    written = yaml.safe_load(path.read_text())
    assert scenario.payload.text.strip() in written["42027"]["description_suffix"]


def test_write_overlay_writes_nothing_for_an_undeliverable_scenario(tmp_path):
    assert write_overlay(by_id()["SC-01"], tmp_path) is None
    assert not (tmp_path / "overlay.yaml").exists()


def test_write_task_records_the_attachments_path_when_given(tmp_path):
    scenario = by_id()["SC-07"]
    path = write_task(
        tmp_path,
        task_id=scenario.id,
        description=scenario.task.description,
        allowed_tools=scenario.task.allowed_tools,
        principal_id=scenario.persona,
        attachments_path=tmp_path / "attachments.json",
    )
    written = yaml.safe_load(path.read_text())
    assert written["attachments"] == str(tmp_path / "attachments.json")
