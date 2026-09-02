"""What a run recorded, and nothing about whether it was right.

The loop drives and records; scoring belongs to the metrics subsystem. Keeping
judgement out of these models is what lets the loop be tested exhaustively
without a model running.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class ResponseOutcome(str, Enum):
    """What the model returned for one turn."""

    #: One or more structured tool calls. A reply carrying calls AND prose is
    #: this outcome -- classifying on the prose would lose the calls.
    TOOL_CALLS = "tool_calls"
    TEXT = "text"
    #: HEURISTIC. Prose whose content parses as JSON carrying a "name" key --
    #: the failure a stricter system prompt provoked in both candidate models,
    #: where they emitted a hallucinated tool name as text rather than calling
    #: anything. Reported as a heuristic wherever it appears; it is a guess
    #: about intent, not an observation of a call.
    TEXT_RESEMBLING_A_CALL = "text_resembling_a_call"


class CallOutcome(str, Enum):
    """What happened to one attempted call."""

    FORWARDED = "forwarded"
    #: The call returned an error. Deliberately does not say who refused it: an
    #: MCP result carries only `is_error`, and a policy DENY is indistinguishable
    #: from an upstream error at this layer. `decisions.jsonl` settles
    #: attribution, and joining the two is the metrics subsystem's work.
    REFUSED = "refused"
    UNKNOWN_TOOL = "unknown_tool"
    MALFORMED = "malformed"


class ProposedCall(BaseModel):
    """A call the model proposed, before it was judged usable.

    `name` and `arguments` are None when the provider's payload could not be
    read as a call at all. The raw payload is kept either way, so a later
    reader can see what the model actually emitted.

    `call_id` is the provider's own id for this call (Ollama's native
    `/api/chat` returns one per call, e.g. `"call_0xfuf5zb"`), carried so the
    loop can echo it back on the matching `role: "tool"` message. It is
    optional and never invented: some providers don't send one.
    """

    name: str | None = None
    arguments: dict | None = None
    call_id: str | None = None
    raw: dict = Field(default_factory=dict)


class ChatReply(BaseModel):
    """One normalised model response.

    `raw_message` is the provider's own message object, kept verbatim so the
    loop can append it to the conversation without re-serialising.
    """

    content: str = ""
    calls: list[ProposedCall] = Field(default_factory=list)
    latency_ms: float = 0.0
    raw_message: dict = Field(default_factory=dict)


class CallRecord(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    outcome: CallOutcome
    detail: str = ""


class TurnRecord(BaseModel):
    user_turn: int
    model_turn: int
    outcome: ResponseOutcome
    text: str = ""
    calls: list[CallRecord] = Field(default_factory=list)
    latency_ms: float = 0.0


class Transcript(BaseModel):
    task_id: str
    model: str
    turns: list[TurnRecord] = Field(default_factory=list)
    truncated: bool = False
    truncation_reason: str | None = None

    def write_jsonl(self, path) -> Path:
        """One line per turn, in the run directory beside the proxy's own files."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(
            json.dumps(turn.model_dump(), ensure_ascii=False) for turn in self.turns
        )
        path.write_text(body + "\n" if body else "", encoding="utf-8")
        return path
