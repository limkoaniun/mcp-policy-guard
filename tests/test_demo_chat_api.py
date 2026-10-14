"""The chat API: a session, and a message streamed as it happens."""

import json
import pathlib
import tempfile

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


def test_a_new_session_starts_with_no_attachments_and_no_erequests():
    body = TestClient(app_with()).post("/api/chat", json={"persona": "S9000004"}).json()

    assert body["attachments"] == []
    assert body["erequests"] == []


DOCUMENTS = REPO / "data" / "documents"


def upload(client, session, path, name=None, content_type="application/octet-stream"):
    path = pathlib.Path(path)
    return client.post(
        f"/api/chat/{session}/upload",
        files={"file": (name or path.name, path.read_bytes(), content_type)},
    )


def upload_bytes(client, session, name, data, content_type="application/octet-stream"):
    return client.post(f"/api/chat/{session}/upload", files={"file": (name, data, content_type)})


def test_uploading_the_as_received_pdf_tags_it_and_serves_its_text():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_attachment", {
            "student_id": "S9000004", "attachment_id": "transfer-checklist"})]),
        ChatReply(content="Read it."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_attachment"]})

    body = upload(client, session, DOCUMENTS / "as-received" / "transfer_checklist.pdf").json()

    [attachment] = body["attachments"]
    assert attachment["id"] == "transfer-checklist"
    assert attachment["name"] == "transfer_checklist.pdf"
    assert attachment["kind"] == "pdf"
    assert attachment["known"]["scenario_id"] == "A-02"
    assert attachment["known"]["variant"] == "planted"
    assert "transfer_checklist.pdf" in attachment["known"]["prompt"]
    assert "NOTE FOR THE ASSISTANT" in attachment["text"]

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "read"}))[-1]
    assert "NOTE FOR THE ASSISTANT" in done["calls"][0]["result"]


def test_the_planted_footer_is_findable_in_the_extracted_text():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000001"}).json()["id"]

    body = upload(client, session, DOCUMENTS / "as-received" / "RPL_application_template.pdf").json()

    [attachment] = body["attachments"]
    assert attachment["known"]["payload_text"] in attachment["text"]


def test_the_clean_pdf_is_tagged_clean_without_a_payload():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    body = upload(client, session, DOCUMENTS / "clean" / "transfer_checklist.pdf").json()

    assert body["attachments"][0]["known"]["variant"] == "clean"
    assert "payload_text" not in body["attachments"][0]["known"]


def test_a_text_upload_is_accepted_and_untagged():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    body = upload_bytes(client, session, "notes.txt", b"My own notes.\n").json()

    [attachment] = body["attachments"]
    assert attachment == {"id": "notes", "name": "notes.txt", "text": "My own notes.\n",
                          "kind": "txt", "known": None}


def test_a_file_name_with_spaces_resolves_by_name():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_attachment", {
            "student_id": "S9000004", "attachment_id": "My Notes.txt"})]),
        ChatReply(content="Read it."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_attachment"]})
    body = upload_bytes(client, session, "My Notes.txt", b"spaced\n").json()
    assert body["attachments"][0]["id"] == "my-notes"

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "read"}))[-1]

    assert done["calls"][0]["blocked"] is False
    assert "spaced" in done["calls"][0]["result"]


def test_uploading_the_same_file_again_replaces_it():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload_bytes(client, session, "notes.txt", b"first\n")

    body = upload_bytes(client, session, "notes.txt", b"second\n").json()

    assert [a["text"] for a in body["attachments"]] == ["second\n"]


def test_other_types_are_refused_naming_the_type():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, "photo.png", b"\x89PNG\r\n", "image/png")

    assert response.status_code == 400
    assert ".png" in response.json()["detail"]


def test_an_oversize_upload_is_refused():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, "big.txt", b"x" * (2 * 1024 * 1024 + 1))

    assert response.status_code == 413


def test_an_over_long_file_name_is_refused():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, ("x" * 300) + ".txt", b"hi")

    assert response.status_code == 400
    assert "too long" in response.json()["detail"]

    accepted = upload_bytes(client, session, ("y" * 196) + ".txt", b"hi")

    assert accepted.status_code == 200


def test_a_dot_or_empty_file_name_is_refused():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    # "/" stands in for a bare empty name: a literal "" is not something a
    # multipart client sends as a file's name at all (the test client falls
    # back to a plain form field, which FastAPI itself rejects with a 422
    # before this route ever sees it), but "/" reaches the route and
    # `Path("/").name` is "" the same way, so it exercises the same check.
    for name in ("..", ".", "/"):
        response = upload_bytes(client, session, name, b"hi")

        assert response.status_code == 400, name
        assert "file name" in response.json()["detail"], name


def test_a_pdf_with_no_text_is_refused():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, "scan.pdf", bytes(pdf.output()), "application/pdf")

    assert response.status_code == 400
    assert "OCR" in response.json()["detail"]


def test_the_uploaded_file_is_kept_as_evidence():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload_bytes(client, session, "notes.txt", b"kept\n")

    # The session root is under the app's temp dir; find it by the id.
    kept = list(pathlib.Path(tempfile.gettempdir()).glob(f"demo-chat-*/{session}/uploads/notes/notes.txt"))
    assert kept and kept[0].read_bytes() == b"kept\n"


def test_deleting_an_attachment_removes_it():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload_bytes(client, session, "notes.txt", b"bye\n")

    body = client.delete(f"/api/chat/{session}/attachments/notes").json()

    assert body["attachments"] == []
    assert client.delete(f"/api/chat/{session}/attachments/notes").status_code == 404


def test_the_attach_menu_endpoint_is_gone():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = client.put(f"/api/chat/{session}/attachments",
                          json={"attached": [{"id": "transfer-checklist", "variant": "planted"}]})

    # The route is gone entirely rather than left registered under a new
    # method, so Starlette reports it the way it reports any unknown path:
    # 404, with no Allow header -- there is no partial match to name one.
    assert response.status_code == 404


def test_the_done_event_carries_the_turn_the_attachments_and_the_erequests():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("submit_erequest", {
            "student_id": "S9000004", "text": "Please waive it.", "type": "requisite_waiver",
            "attachment_ids": ["transfer-checklist"]})]),
        ChatReply(content="Submitted."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload(client, session, DOCUMENTS / "clean" / "transfer_checklist.pdf")

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "waive"}))[-1]

    assert done["turn"] == 1
    assert done["attachments"][0]["known"]["variant"] == "clean"
    assert done["erequests"][0]["text"] == "Please waive it."
    assert done["erequests"][0]["attachment_ids"] == ["transfer-checklist"]
    assert done["calls"][0]["tainted"] is False


def test_approving_a_held_call_after_an_upload():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_attachment", {
            "student_id": "S9000004", "attachment_id": "transfer-checklist"})]),
        ChatReply(content="", calls=[call("drop", {"student_id": "S9000004", "code": "32541"})]),
        ChatReply(content="I need your confirmation."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": [
        "get_attachment", "get_study_plan", "drop"]})
    upload(client, session, DOCUMENTS / "as-received" / "transfer_checklist.pdf")
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


def test_a_running_agent_message_leaves_the_server_responsive():
    """A model call takes ten to thirty seconds. The agent arm must not hold
    the event loop for that long: other requests, and the 409 the busy guards
    exist to send, have to get through while it runs."""
    from threading import Event, Thread

    entered, release = Event(), Event()
    gave_up = []

    def chat(*_):
        entered.set()
        # A blocked loop cannot serve the requests below until this returns,
        # so a timeout here is the failure this test exists to catch: the
        # requests only got through because the model stopped waiting.
        if not release.wait(10):
            gave_up.append(True)
        return ChatReply(content="Done.")

    client = TestClient(create_app(chat_factory=lambda model: chat))
    with client:
        session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
        base = f"/api/chat/{session}"
        worker = Thread(target=client.post, args=(base + "/message",),
                        kwargs={"json": {"text": "my plan?"}})
        worker.start()
        try:
            assert entered.wait(5)
            assert client.get("/api/personas").status_code == 200
            assert client.put(base + "/mode", json={"mode": "rag"}).status_code == 409
            assert client.post(base + "/reset").status_code == 409
        finally:
            release.set()
            worker.join(10)
        assert not worker.is_alive()
        assert not gave_up, "the server only answered once the model call timed out"


def test_a_call_for_another_student_reaches_the_page_with_its_code():
    """The breach warning keys on `code`, never on the reason text."""
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_student_record", {"student_id": "S9000001"})]),
        ChatReply(content="Here is Jane's record."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = client.post(f"/api/chat/{session}/message", json={"text": "show me Jane"})
    done = events_from(response)[-1]

    assert done["calls"][0]["verdict"] == "deny"
    assert done["calls"][0]["code"] == "principal_mismatch"


def test_a_call_for_another_student_on_the_off_arm_still_carries_its_code():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_student_record", {"student_id": "S9000001"})]),
        ChatReply(content="Here is Jane's record."),
    ))
    session = client.post(
        "/api/chat", json={"persona": "S9000004", "mode": "agent-off"}
    ).json()["id"]

    response = client.post(f"/api/chat/{session}/message", json={"text": "show me Jane"})
    done = events_from(response)[-1]

    assert done["calls"][0]["code"] == "principal_mismatch"
    assert done["calls"][0]["blocked"] is False


def test_an_ordinary_call_has_a_null_code_on_the_page():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_study_plan", {"student_id": "S9000004"})]),
        ChatReply(content="Here is your plan."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "plan"}))[-1]

    assert done["calls"][0]["code"] is None
