"""RAG's real API and artifacts, with an injected model and no MCP execution."""

import json
import tempfile
from pathlib import Path
from threading import Event, Thread

from fastapi.testclient import TestClient

from enrolment.agent.transcript import ChatReply, ProposedCall
from enrolment.demo.app import create_app


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def setup(chat=None, persona="S9000001"):
    seen = []

    def model(messages, tools):
        seen.append((messages, tools))
        return chat(messages, tools) if chat else ChatReply(content="Course advice.", latency_ms=12)

    client = TestClient(create_app(chat_factory=lambda _: model, available_models=lambda: ["test-model"]))
    state = client.post("/api/chat", json={"mode": "rag", "persona": persona}).json()
    return client, state, seen


def send(client, state, text):
    return events(client.post(f"/api/chat/{state['id']}/message", json={"text": text, "model": "test-model"}))


def test_rag_uses_current_record_and_subject_without_tools_or_state_changes():
    client, state, seen = setup()
    result = send(client, state, "Tell me about 32513")
    done = result[-1]
    assert [e["kind"] for e in result] == ["said", "done"]
    assert done["mode"] == "rag" and done["calls"] == []
    assert done["record"] == state["record"] and done["erequests"] == []
    assert seen[0][1] == []
    assert any(b.startswith("Subject 32513") for b in done["context"])
    assert "S9000001" in seen[0][0][-1]["content"]
    roots = list(Path(tempfile.gettempdir()).glob(f"demo-chat-*/{state['id']}"))
    artifact = json.loads((roots[0] / "turn-001" / "rag.json").read_text(encoding="utf-8"))
    assert artifact["messages"] == seen[0][0]
    assert artifact["latency_ms"] == 12
    assert not (roots[0] / "turn-001" / "decisions.jsonl").exists()


def test_upload_pdf_reaches_rag_and_old_context_is_a_snapshot():
    client, state, seen = setup()
    pdf = Path(__file__).resolve().parents[1] / "data/documents/as-received/RPL_application_template.pdf"
    response = client.post(f"/api/chat/{state['id']}/upload", files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")})
    assert response.status_code == 200
    attachment = response.json()["attachments"][0]
    done = send(client, state, "Explain this form")[-1]
    assert attachment["text"] in seen[0][0][-1]["content"]
    assert "known" not in seen[0][0][-1]["content"]
    client.delete(f"/api/chat/{state['id']}/attachments/{attachment['id']}")
    later = send(client, state, "Tell me about 32513")[-1]
    assert not any(b.startswith("Uploaded document") for b in later["context"])
    assert any(attachment["text"] in b for b in done["context"])


def test_follow_up_retrieves_previous_subject_and_keeps_dialogue():
    client, state, seen = setup()
    send(client, state, "Tell me about 32513")
    done = send(client, state, "Am I eligible for it?")[-1]
    assert any(b.startswith("Subject 32513") for b in done["context"])
    assert [m["role"] for m in seen[1][0]] == ["system", "user", "assistant", "user"]


def test_next_session_clarifies_then_retrieves_course_structure():
    client, state, seen = setup()
    first = send(client, state, "What can I take next session?")[-1]
    assert first["clarification"] and not seen
    done = send(client, state, "Spring")[-1]
    assert not done["clarification"] and len(seen) == 1
    assert any(b.startswith("Course structure") for b in done["context"])
    assert "What can I take next session?" in seen[0][0][1]["content"]


def test_explicit_session_and_subject_are_both_retrieved():
    client, state, _ = setup()
    done = send(client, state, "Can I take 32513 in Autumn?")[-1]
    assert any(b.startswith("Subject 32513") for b in done["context"])
    assert any(b.startswith("Course structure") for b in done["context"])


def test_mode_changes_clear_history_and_approvals_but_keep_attachments():
    client, state, seen = setup()
    base = f"/api/chat/{state['id']}"
    client.post(base + "/upload", files={"file": ("notes.txt", b"document contents")})
    send(client, state, "Tell me about 32513")
    for mode in ("agent-off", "agent-on", "rag"):
        changed = client.put(base + "/mode", json={"mode": mode})
        assert changed.status_code == 200
        assert changed.json()["mode"] == mode
        assert changed.json()["record"] == state["record"]
        assert changed.json()["attachments"][0]["text"] == "document contents"
    send(client, state, "Tell me about 32933")
    assert len(seen[-1][0]) == 2
    assert client.post(base + "/approve", json={"turn": 1, "index": 0}).status_code == 409
    assert client.put(base + "/enforcement", json={"enforcement": "off"}).status_code == 409


def test_new_persona_has_own_record_and_no_shared_history_or_uploads():
    client, state, seen = setup()
    client.post(f"/api/chat/{state['id']}/upload", files={"file": ("notes.txt", b"private note")})
    send(client, state, "Tell me about 32513")
    other = client.post("/api/chat", json={"mode": "rag", "persona": "S9000004"}).json()
    done = send(client, other, "Tell me about 32933")[-1]
    assert done["attachments"] == [] and done["record"]["student_id"] == "S9000004"
    assert len(seen[-1][0]) == 2
    assert "private note" not in seen[-1][0][-1]["content"]


def test_large_context_is_refused_without_calling_model_and_retry_works():
    client, state, seen = setup()
    base = f"/api/chat/{state['id']}"
    client.post(base + "/upload", files={"file": ("long.txt", b"a" * 25000)})
    failed = send(client, state, "Summarise the document")
    assert failed[-1]["kind"] == "error" and "too long" in failed[-1]["detail"]
    assert seen == []
    client.delete(base + "/attachments/long")
    assert send(client, state, "Tell me about 32513")[-1]["kind"] == "done"


def test_provider_failure_can_retry_without_failed_history():
    attempts = []

    def chat(messages, tools):
        attempts.append(messages)
        if len(attempts) == 1:
            raise ConnectionError("model offline")
        return ChatReply(content="Back online.")

    client, state, _ = setup(chat)
    assert send(client, state, "Tell me about 32513")[-1]["kind"] == "error"
    assert send(client, state, "Tell me about 32933")[-1]["kind"] == "done"
    assert len(attempts[-1]) == 2


def test_model_tool_proposal_cannot_execute_in_rag():
    client, state, _ = setup(lambda *_: ChatReply(content="", calls=[
        ProposedCall(name="drop", arguments={"student_id": "S9000001", "code": "32513"})]))
    result = send(client, state, "Drop 32513")
    assert result[-1]["kind"] == "error" and "no action ran" in result[-1]["detail"]


def test_busy_rag_keeps_event_loop_responsive_and_rejects_mutations():
    entered, release = Event(), Event()

    def chat(*_):
        entered.set()
        assert release.wait(10)
        return ChatReply(content="Done.")

    client, state, _ = setup(chat)
    with client:
        worker = Thread(target=send, args=(client, state, "Tell me about 32513"))
        worker.start()
        try:
            assert entered.wait(5)
            base = f"/api/chat/{state['id']}"
            assert client.get("/api/personas").status_code == 200
            assert client.put(base + "/mode", json={"mode": "agent-on"}).status_code == 409
            assert client.post(base + "/reset").status_code == 409
            assert client.post(base + "/message", json={"text": "second"}).status_code == 409
        finally:
            release.set()
            worker.join(10)
        assert not worker.is_alive()


def test_invalid_mode_and_empty_question_are_refused():
    client, state, _ = setup()
    assert client.put(f"/api/chat/{state['id']}/mode", json={"mode": "other"}).status_code == 422
    assert client.post(f"/api/chat/{state['id']}/message", json={"text": " "}).status_code == 400


def test_mentioning_a_session_is_not_a_request_to_choose_one():
    """'per session' is a unit, not a plan; the credit cap is answerable from
    the record alone, so the model is called instead of being gated."""
    client, state, seen = setup()
    done = send(client, state, "What is my credit cap per session?")[-1]
    assert not done["clarification"] and len(seen) == 1


def test_asking_which_of_two_seasons_a_subject_runs_in_is_answered_not_gated():
    client, state, seen = setup()
    done = send(client, state, "Is 32513 offered in Autumn or Spring?")[-1]
    assert not done["clarification"] and len(seen) == 1
    assert any(b.startswith("Subject 32513") for b in done["context"])


def test_a_reply_that_names_no_season_keeps_the_pending_question():
    client, state, seen = setup()
    assert send(client, state, "What can I take next session?")[-1]["clarification"]
    again = send(client, state, "the first one")[-1]
    assert again["clarification"] and not seen
    done = send(client, state, "Spring")[-1]
    assert not done["clarification"] and len(seen) == 1
    assert "What can I take next session?" in seen[0][0][1]["content"]
