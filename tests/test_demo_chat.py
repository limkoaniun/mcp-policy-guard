"""A live chat session against the policy layer.

Unlike `demo.session`, which runs one scenario end to end, this holds a
conversation: many messages against one accumulating student record, with the
allowlist and the enforcement switch changeable between messages.

Each message runs its own short-lived proxy, seeded from the record the last
message left behind. That is what makes a mid-conversation allowlist change
free -- the next message simply starts a proxy that was told something
different -- and it keeps `state.RunDirectoryInUseError`'s single-use rule
intact rather than working around it.
"""

import pathlib

import pytest
from test_agent_loop import call, scripted

from enrolment.agent.transcript import ChatReply
from enrolment.demo.chat import ChatSession

REPO = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO / "configs" / "enrolment-personas.yaml"

READ_ONLY = ["search_subjects", "get_subject_details", "get_student_record",
             "check_prerequisites", "get_study_plan", "get_session_options"]


def session(tmp_path, **overrides) -> ChatSession:
    base = {
        "persona": "S9000004", "root": tmp_path / "session", "pack_path": PACK,
        "repo_root": REPO, "allowed_tools": [*READ_ONLY, "drop"],
    }
    base.update(overrides)
    return ChatSession(**base)


@pytest.mark.anyio
async def test_a_message_reports_the_calls_the_model_made(tmp_path):
    reply = await session(tmp_path).send(
        "what is my study plan?",
        chat=scripted(ChatReply(content="", calls=[
            call("get_study_plan", {"student_id": "S9000004"})])),
        model="fake",
    )

    assert [c.tool for c in reply.calls] == ["get_study_plan"]
    assert reply.calls[0].blocked is False


@pytest.mark.anyio
async def test_a_second_message_continues_the_same_conversation(tmp_path):
    live = session(tmp_path)
    await live.send("first", chat=scripted(ChatReply(content="one")), model="fake")
    await live.send("second", chat=scripted(ChatReply(content="two")), model="fake")

    assert [m["content"] for m in live.history if m["role"] == "user"] == ["first", "second"]


@pytest.mark.anyio
async def test_unticking_a_tool_denies_it_on_the_next_message(tmp_path):
    """The allowlist is the page's checkboxes. Campaign ablations never
    exercised it -- no run proposed an undeclared tool -- so this is where the
    policy can actually be shown working."""
    live = session(tmp_path)
    live.allowed_tools = list(READ_ONLY)  # drop unticked

    reply = await live.send(
        "drop 32541",
        chat=scripted(ChatReply(content="", calls=[
            call("drop", {"student_id": "S9000004", "code": "32541"})])),
        model="fake",
    )

    assert reply.calls[0].blocked is True
    assert reply.calls[0].policy == "allowlist"


@pytest.mark.anyio
async def test_the_record_accumulates_across_messages(tmp_path):
    """Each message starts a fresh proxy seeded from the last one's record, so
    a mutation in message one is still there in message two."""
    live = session(tmp_path, enforcement="off")
    before = live.record()["enrolled"]

    await live.send("drop it", chat=scripted(ChatReply(content="", calls=[
        call("drop", {"student_id": "S9000004", "code": "32541"})])), model="fake")
    after_one = live.record()["enrolled"]

    await live.send("anything else?", chat=scripted(ChatReply(content="ok")), model="fake")
    after_two = live.record()["enrolled"]

    assert "32541" in before
    assert "32541" not in after_one
    assert after_two == after_one, "the second message must not reset the record"
