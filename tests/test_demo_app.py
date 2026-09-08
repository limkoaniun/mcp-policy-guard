"""The demo web app. Endpoints only -- the page itself is checked for the
controls it must carry, not for how it looks."""

import pathlib

from fastapi.testclient import TestClient

from enrolment.demo.app import create_app

REPO = pathlib.Path(__file__).resolve().parents[1]


def client() -> TestClient:
    return TestClient(create_app())


def test_it_lists_which_scenarios_can_be_shown_on_both_arms():
    body = client().get("/api/scenarios").json()
    by_id = {s["id"]: s for s in body}

    assert by_id["SC-06"]["has_off_arm"] is True
    assert by_id["SC-01"]["has_off_arm"] is False, "tool-shadowing has no delivery mechanism"


def test_running_the_off_arm_reports_the_subject_the_block_would_have_saved():
    body = client().post(
        "/api/run", json={"scenario_id": "SC-06", "enforcement": "off"}
    ).json()

    assert "32541" in body["enrolled_before"]
    assert "32541" not in body["enrolled_after"]
    assert body["driver"] == "scripted"


def test_asking_for_an_off_arm_that_does_not_exist_is_refused_not_faked():
    response = client().post(
        "/api/run", json={"scenario_id": "SC-01", "enforcement": "off"}
    )

    assert response.status_code == 400
    assert "deliverable" in response.json()["detail"]


def test_the_page_carries_the_toggle_and_names_its_driver():
    """Two things the page must not be able to ship without: the control the
    demonstration turns on, and the statement that these calls were scripted
    rather than a model's own (brief §6.1)."""
    response = client().get("/")
    page = response.text

    assert response.status_code == 200
    assert 'id="protection"' in page
    assert "/api/run" in page
    assert "scripted" in page.lower()
