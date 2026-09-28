"""The chat API: a session, and a message streamed as it happens."""

import json
import pathlib

from fastapi.testclient import TestClient
from test_agent_loop import call, scripted

from enrolment.agent.transcript import ChatReply
from enrolment.demo.app import create_app

REPO = pathlib.Path(__file__).resolve().parents[1]


def app_with(*replies):
    return create_app(chat_factory=lambda model: scripted(*replies))


def events_from(response) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_a_new_session_reports_the_record_and_the_tools_it_starts_with():
    body = TestClient(app_with()).post("/api/chat", json={}).json()

    assert body["record"]["student_id"].startswith("S")
    assert "get_study_plan" in body["allowed_tools"]
    assert body["enforcement"] == "on"


def test_a_message_streams_its_events_before_the_result():
    """The page renders as the run unfolds. A single blob at the end would
    leave a viewer watching a spinner for the length of a model call."""
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_study_plan", {"student_id": "S9000004"})]),
        ChatReply(content="Here is your plan."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = client.post(f"/api/chat/{session}/message", json={"text": "my plan?"})
    events = events_from(response)

    kinds = [e["kind"] for e in events]
    assert "call" in kinds
    assert kinds[-1] == "done"
    assert kinds.index("call") < kinds.index("done")


def test_unticking_a_tool_takes_effect_on_the_next_message():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("drop", {"student_id": "S9000004", "code": "32541"})]),
        ChatReply(content="I can't do that."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_study_plan"]})

    events = events_from(client.post(f"/api/chat/{session}/message", json={"text": "drop 32541"}))
    done = events[-1]

    assert done["calls"][0]["blocked"] is True
    assert done["calls"][0]["policy"] == "allowlist"


def test_the_enforcement_switch_takes_effect_on_the_next_message():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("drop", {"student_id": "S9000004", "code": "32541"})]),
        ChatReply(content="Done."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_study_plan"]})
    client.put(f"/api/chat/{session}/enforcement", json={"enforcement": "off"})

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "drop 32541"}))[-1]

    assert done["calls"][0]["verdict"] == "deny", "the engine still rules on it"
    assert done["calls"][0]["blocked"] is False, "but it is not acted on"
    assert "32541" not in done["record"]["enrolled"]


def test_reset_puts_the_record_back_and_keeps_the_console():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("drop", {"student_id": "S9000004", "code": "32541"})]),
        ChatReply(content="Done."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_study_plan", "drop"]})
    client.put(f"/api/chat/{session}/enforcement", json={"enforcement": "off"})
    events_from(client.post(f"/api/chat/{session}/message", json={"text": "drop 32541"}))

    body = client.post(f"/api/chat/{session}/reset").json()

    assert "32541" in body["record"]["enrolled"], "the record is back to baseline"
    assert body["allowed_tools"] == ["get_study_plan", "drop"]
    assert body["enforcement"] == "off"


def attach(client, session, doc_id, variant="planted"):
    return client.put(f"/api/chat/{session}/attachments",
                      json={"attached": [{"id": doc_id, "variant": variant}]})


def test_a_new_session_starts_with_no_attachments_and_no_erequests():
    body = TestClient(app_with()).post("/api/chat", json={"persona": "S9000004"}).json()

    assert body["attachments"] == []
    assert body["erequests"] == []


def test_attaching_a_document_reports_it_with_its_full_text():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    body = attach(client, session, "transfer-checklist").json()

    assert [a["id"] for a in body["attachments"]] == ["transfer-checklist"]
    assert body["attachments"][0]["variant"] == "planted"
    assert body["attachments"][0]["name"] == "transfer_checklist.txt"
    assert "NOTE FOR THE ASSISTANT" in body["attachments"][0]["text"]


def test_attaching_another_students_document_is_refused():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = attach(client, session, "rpl-template")

    assert response.status_code == 400
    assert "S9000001" in response.json()["detail"]


def test_attaching_an_unknown_document_is_refused():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    assert attach(client, session, "nope").status_code == 400


def test_the_done_event_carries_the_turn_the_attachments_and_the_erequests():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("submit_erequest", {
            "student_id": "S9000004", "text": "Please waive it.", "type": "requisite_waiver",
            "attachment_ids": ["transfer-checklist"]})]),
        ChatReply(content="Submitted."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    attach(client, session, "transfer-checklist", "clean")

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "waive"}))[-1]

    assert done["turn"] == 1
    assert done["attachments"][0]["variant"] == "clean"
    assert done["erequests"][0]["text"] == "Please waive it."
    assert done["erequests"][0]["attachment_ids"] == ["transfer-checklist"]
    assert done["calls"][0]["tainted"] is False


def test_approving_a_held_call_returns_the_confirmation_and_the_new_state():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_attachment", {
            "student_id": "S9000004", "attachment_id": "transfer-checklist"})]),
        ChatReply(content="", calls=[call("drop", {"student_id": "S9000004", "code": "32541"})]),
        ChatReply(content="I need your confirmation."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": [
        "get_attachment", "get_study_plan", "drop"]})
    attach(client, session, "transfer-checklist")
    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "go"}))[-1]
    assert done["calls"][1]["verdict"] == "require_approval"
    assert "32541" in done["record"]["enrolled"]

    body = client.post(f"/api/chat/{session}/approve", json={"turn": 1, "index": 1}).json()

    assert body["confirmation"]["reread"] == 1
    assert body["confirmation"]["call"]["verdict"] == "require_approval"
    assert body["confirmation"]["call"]["blocked"] is False
    assert "32541" not in body["record"]["enrolled"]


def test_approving_something_that_was_not_held_is_a_400_with_the_reason():
    client = TestClient(app_with(ChatReply(content="hi")))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    events_from(client.post(f"/api/chat/{session}/message", json={"text": "hello"}))

    response = client.post(f"/api/chat/{session}/approve", json={"turn": 1, "index": 0})

    assert response.status_code == 400
    assert "turn 1" in response.json()["detail"]
