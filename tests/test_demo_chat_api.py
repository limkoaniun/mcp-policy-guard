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
