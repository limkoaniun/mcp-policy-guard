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


def test_the_built_frontend_is_served():
    """The page is a built React bundle whose output is committed. Serving a
    stale or missing build is the failure this catches: the API can be
    entirely correct while the page is not there at all."""
    response = client().get("/")

    assert response.status_code == 200
    assert 'id="root"' in response.text
    assert "/assets/" in response.text
    assert response.headers["cache-control"] == "no-store, must-revalidate"


def test_the_console_declares_every_tool_including_get_attachment():
    """The page draws its checkboxes from this rather than a list of its own,
    so a tool added upstream cannot go missing from the console. The planted
    32513 description is gone: the attack arrives through the attach control."""
    body = client().get("/api/tools").json()

    names = [t["name"] for t in body["tools"]]
    assert "drop" in names
    assert "get_attachment" in names
    assert all(t["side_effect"] for t in body["tools"] if t["name"] in {"enrol", "drop"})
    assert "get_attachment" in body["presets"]["read-only"]
    assert "injected" not in body


def scripted_model(*replies):
    from enrolment.agent.transcript import ChatReply
    queue = list(replies)

    def chat(messages, tools):
        return queue.pop(0) if queue else ChatReply(content="Done.")

    return chat


def proposes(tool, **arguments):
    from enrolment.agent.transcript import ChatReply, ProposedCall
    return ChatReply(content="", calls=[ProposedCall(
        name=tool, arguments=arguments, raw={"function": {"name": tool}})])


def test_running_with_a_model_lets_it_choose_the_calls():
    """`chat_factory` is injectable for the same reason the campaign runners
    make it so: the app is testable with no daemon running."""
    app = create_app(chat_factory=lambda model: scripted_model(
        proposes("get_study_plan", student_id="S9000004"),
        proposes("drop", student_id="S9000004", code="32541"),
    ))
    body = TestClient(app).post("/api/run", json={
        "scenario_id": "SC-06", "enforcement": "on", "model": "fake-model",
    }).json()

    assert body["driver"] == "fake-model"
    assert [c["tool"] for c in body["calls"]] == ["get_study_plan", "drop"]
    assert body["calls"][1]["blocked"] is True


def test_omitting_the_model_still_replays_the_script():
    body = client().post(
        "/api/run", json={"scenario_id": "SC-06", "enforcement": "on"}
    ).json()

    assert body["driver"] == "scripted"


def test_the_page_offers_the_models_that_are_actually_installed():
    """A picker listing a model Ollama does not have produces a run that fails
    for a reason the audience cannot see."""
    app = create_app(available_models=lambda: ["llama3.2", "qwen2.5:14b"])
    body = TestClient(app).get("/api/models").json()

    assert body == ["llama3.2", "qwen2.5:14b"]


def test_the_page_offers_the_personas_with_a_short_description():
    body = client().get("/api/personas").json()
    by_id = {p["id"]: p for p in body}

    assert set(by_id) == {"S9000001", "S9000002", "S9000003", "S9000004"}
    # The description is built from the real record: major title from the
    # catalogue, not the raw MAJ code, plus the record's shape.
    assert by_id["S9000001"]["major"] == "Cybersecurity"
    assert by_id["S9000002"]["major"] == "Internetworking"
    assert by_id["S9000001"]["completed"] == 4
    assert by_id["S9000004"]["enrolled"] == 4


def test_a_session_for_an_unknown_persona_is_refused_not_broken():
    response = client().post("/api/chat", json={"persona": "S9999999"})

    assert response.status_code == 400
    assert "S9999999" in response.json()["detail"]


def test_a_session_for_a_known_persona_starts_on_that_record():
    body = client().post("/api/chat", json={"persona": "S9000004"}).json()

    assert body["record"]["student_id"] == "S9000004"
    assert body["record"]["enrolled"] == ["32541", "43030", "32548", "32547"]


def test_the_picker_gets_the_students_names():
    body = client().get("/api/personas").json()
    by_id = {p["id"]: p for p in body}

    assert by_id["S9000001"]["name"] == "Jane Doe"
    assert by_id["S9000004"]["name"] == "Tom Becker"


def test_it_lists_one_attachable_document_per_upload_persona():
    body = client().get("/api/documents").json()

    assert sorted(d["persona"] for d in body) == ["S9000001", "S9000002", "S9000003", "S9000004"]
    jane = next(d for d in body if d["persona"] == "S9000001")
    assert jane["name"] == "RPL_application_template.txt"
    assert jane["planted_text"].endswith(jane["payload_text"])
    assert jane["prompt"], "the scenario's request is offered as a chip"
