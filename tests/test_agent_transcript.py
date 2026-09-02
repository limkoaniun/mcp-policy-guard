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
        "tool_calls", "text", "text_resembling_a_call", "provider_error",
    }
    assert {o.value for o in CallOutcome} == {
        "forwarded", "refused", "unknown_tool", "malformed", "not_attempted",
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


def _sample_transcript(*, truncated: bool, truncation_reason: str | None) -> Transcript:
    return Transcript(
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
        truncated=truncated,
        truncation_reason=truncation_reason,
    )


def test_a_transcript_round_trips_through_jsonl(tmp_path):
    """Every field survives the trip through disk, including the ones that
    live only on the Transcript, not on any TurnRecord -- truncated and
    truncation_reason chief among them, since nothing caught their absence
    before this test actually round-tripped anything."""
    transcript = _sample_transcript(
        truncated=True, truncation_reason="max_calls (12) reached"
    )
    path = tmp_path / "transcript.jsonl"
    transcript.write_jsonl(path)

    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 2, "a header line plus one turn line"
    assert lines[0] == {
        "task_id": "T-01",
        "model": "llama3.2:latest",
        "truncated": True,
        "truncation_reason": "max_calls (12) reached",
    }

    restored = Transcript.read_jsonl(path)
    assert restored.task_id == "T-01"
    assert restored.model == "llama3.2:latest"
    assert restored.truncated is True
    assert restored.truncation_reason == "max_calls (12) reached"
    assert len(restored.turns) == 1
    assert restored.turns[0].outcome is ResponseOutcome.TOOL_CALLS
    assert restored.turns[0].latency_ms == 1234.5
    assert restored.turns[0].calls[0].outcome is CallOutcome.REFUSED
    assert restored.turns[0].calls[0].arguments == {
        "student_id": "S9000003", "code": "32548",
    }


def test_a_truncated_file_is_not_identical_to_a_complete_one(tmp_path):
    """Before the header line, a run cut short by `max_calls` and a run the
    model finished were byte-identical on disk -- the truncation was real but
    invisible. Confirms it no longer is."""
    complete = _sample_transcript(truncated=False, truncation_reason=None)
    truncated = _sample_transcript(
        truncated=True, truncation_reason="max_calls (12) reached"
    )

    complete_path = tmp_path / "complete.jsonl"
    truncated_path = tmp_path / "truncated.jsonl"
    complete.write_jsonl(complete_path)
    truncated.write_jsonl(truncated_path)

    assert complete_path.read_bytes() != truncated_path.read_bytes()
    assert Transcript.read_jsonl(complete_path).truncated is False
    assert Transcript.read_jsonl(truncated_path).truncated is True


def test_a_truncated_run_with_no_turns_still_shows_it_on_disk(tmp_path):
    """A run with an empty `task.turns` previously wrote a zero-byte file
    with `truncated=False` implied by its very absence -- a run that did
    nothing read as a run that finished cleanly. The header line fixes this:
    confirm it does."""
    transcript = Transcript(
        task_id="T", model="m", turns=[],
        truncated=True, truncation_reason="max_model_turns (0) reached",
    )
    path = tmp_path / "transcript.jsonl"
    transcript.write_jsonl(path)

    assert path.stat().st_size > 0

    restored = Transcript.read_jsonl(path)
    assert restored.truncated is True
    assert restored.truncation_reason == "max_model_turns (0) reached"
    assert restored.turns == []


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
