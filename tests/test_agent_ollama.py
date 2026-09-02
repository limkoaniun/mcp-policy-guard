"""Parsing is tested against payloads recorded from a real daemon; the one live
test skips loudly when none is running."""

import json
import urllib.error
import urllib.request

import pytest

from enrolment.agent.ollama import (
    OLLAMA_URL,
    model_fingerprint,
    ollama_chat,
    parse_reply,
    warm_up,
)
from enrolment.agent.transcript import ChatReply

# Recorded verbatim from ollama 0.33.2, llama3.2:latest.
WITH_CALLS = {
    "model": "llama3.2:latest",
    "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_0xfuf5zb",
                "function": {
                    "index": 0,
                    "name": "get_subject_details",
                    "arguments": {"code": "32541"},
                },
            }
        ],
    },
    "done_reason": "stop",
    "total_duration": 239166000,
}

TEXT_ONLY = {
    "model": "llama3.2:latest",
    "message": {"role": "assistant", "content": "Subject 32541 is Project Management."},
    "done_reason": "stop",
    "total_duration": 500000000,
}


def test_parse_reply_reads_a_dict_arguments_payload():
    reply = parse_reply(WITH_CALLS)
    assert isinstance(reply, ChatReply)
    assert len(reply.calls) == 1
    assert reply.calls[0].name == "get_subject_details"
    assert reply.calls[0].arguments == {"code": "32541"}
    assert reply.calls[0].call_id == "call_0xfuf5zb"


def test_parse_reply_reads_a_string_arguments_payload():
    """Older Ollama versions and some models return arguments as a JSON string."""
    payload = json.loads(json.dumps(WITH_CALLS))
    payload["message"]["tool_calls"][0]["function"]["arguments"] = '{"code": "32541"}'

    reply = parse_reply(payload)

    assert reply.calls[0].arguments == {"code": "32541"}


def test_parse_reply_marks_unreadable_arguments_rather_than_guessing():
    payload = json.loads(json.dumps(WITH_CALLS))
    payload["message"]["tool_calls"][0]["function"]["arguments"] = "not json at all"

    reply = parse_reply(payload)

    assert reply.calls[0].name == "get_subject_details"
    assert reply.calls[0].arguments is None
    assert reply.calls[0].raw != {}


def test_parse_reply_keeps_prose_that_arrives_beside_calls():
    payload = json.loads(json.dumps(WITH_CALLS))
    payload["message"]["content"] = "Let me look that up."

    reply = parse_reply(payload)

    assert reply.content == "Let me look that up."
    assert len(reply.calls) == 1


def test_parse_reply_handles_a_text_only_response():
    reply = parse_reply(TEXT_ONLY)
    assert reply.calls == []
    assert reply.content == "Subject 32541 is Project Management."


def test_parse_reply_converts_duration_to_milliseconds():
    """Ollama reports total_duration in nanoseconds."""
    reply = parse_reply(WITH_CALLS)
    assert reply.latency_ms == pytest.approx(239.166, rel=1e-3)


def test_parse_reply_keeps_the_raw_message_for_the_conversation():
    reply = parse_reply(WITH_CALLS)
    assert reply.raw_message == WITH_CALLS["message"]


def _daemon_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=2):
            return True
    except (urllib.error.URLError, OSError):
        return False


LIVE = pytest.mark.skipif(
    not _daemon_is_up(),
    reason=(
        "no Ollama daemon responding at "
        f"{OLLAMA_URL} -- start it with `ollama serve` to run the live tests. "
        "Skipped, not passed: this test proves nothing when it does not run."
    ),
)


@LIVE
def test_model_fingerprint_names_what_produced_a_number():
    """A number without the weights that produced it is not reproducible."""
    fingerprint = model_fingerprint("llama3.2:latest")

    assert fingerprint["model"] == "llama3.2:latest"
    assert len(fingerprint["digest"]) >= 12
    assert fingerprint["ollama_version"].count(".") >= 1


@LIVE
def test_a_live_chat_returns_a_parsed_reply():
    warm_up("llama3.2:latest")
    chat = ollama_chat("llama3.2:latest")

    reply = chat(
        [{"role": "user", "content": "Say the single word: ready"}],
        [],
    )

    assert isinstance(reply, ChatReply)
    assert reply.content.strip() != ""
    assert reply.latency_ms > 0
