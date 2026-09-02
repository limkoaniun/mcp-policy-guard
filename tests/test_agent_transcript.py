import json

from enrolment.agent.transcript import (
    CallOutcome,
    CallRecord,
    ChatReply,
    ProposedCall,
    ResponseOutcome,
    Transcript,
    TurnRecord,
)


def test_outcome_enums_carry_exactly_the_specified_members():
    assert {o.value for o in ResponseOutcome} == {
        "tool_calls", "text", "text_resembling_a_call",
    }
    assert {o.value for o in CallOutcome} == {
        "forwarded", "refused", "unknown_tool", "malformed",
    }


def test_a_proposed_call_may_be_unparseable():
    """The model's output is untrusted: name and arguments may be absent."""
    bad = ProposedCall(name=None, arguments=None, raw={"garbage": True})
    assert bad.name is None and bad.arguments is None and bad.raw == {"garbage": True}


def test_a_chat_reply_defaults_to_no_calls():
    reply = ChatReply(content="hello")
    assert reply.calls == []
    assert reply.latency_ms == 0.0
    assert reply.raw_message == {}


def test_a_transcript_round_trips_through_jsonl(tmp_path):
    transcript = Transcript(
        task_id="T-01",
        model="llama3.2:latest",
        turns=[
            TurnRecord(
                user_turn=0,
                model_turn=0,
                outcome=ResponseOutcome.TOOL_CALLS,
                text="",
                calls=[
                    CallRecord(
                        tool="enrol",
                        arguments={"student_id": "S9000003", "code": "32548"},
                        outcome=CallOutcome.REFUSED,
                        detail="blocked by an anti-requisite",
                    )
                ],
                latency_ms=1234.5,
            )
        ],
    )
    path = tmp_path / "transcript.jsonl"
    transcript.write_jsonl(path)

    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["outcome"] == "tool_calls"
    assert lines[0]["calls"][0]["outcome"] == "refused"
    assert lines[0]["calls"][0]["arguments"] == {"student_id": "S9000003", "code": "32548"}


def test_a_transcript_records_truncation_with_a_reason():
    """A run that hit a bound must say so; a silent truncation would read as a
    model that simply stopped."""
    transcript = Transcript(task_id="T", model="m", turns=[])
    assert transcript.truncated is False
    assert transcript.truncation_reason is None

    hit = Transcript(
        task_id="T", model="m", turns=[],
        truncated=True, truncation_reason="max_calls (12) reached",
    )
    assert hit.truncated is True
    assert "12" in hit.truncation_reason
