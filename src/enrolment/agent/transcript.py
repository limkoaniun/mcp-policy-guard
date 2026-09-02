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
    #: The chat callable raised instead of returning a reply -- a transport
    #: failure (timeout, connection error, an unparseable body) rather than
    #: anything the model said. Recorded so the turns already gathered before
    #: the failure survive in the returned transcript; the run is marked
    #: truncated with the exception named in the reason. Never a judgement
    #: about the model: the provider, not the model, failed to answer.
    PROVIDER_ERROR = "provider_error"


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
    #: The proposal was well-formed and named a real, known tool, but the
    #: harness never sent it -- a bound the harness itself imposes (e.g.
    #: `max_calls`) was reached first. This must never be counted as a model
    #: error: the model did nothing wrong, our own bookkeeping did. Kept
    #: distinct from MALFORMED and UNKNOWN_TOOL, which measure a genuine
    #: property of the model's output and must stay uncontaminated by
    #: harness-caused truncation.
    NOT_ATTEMPTED = "not_attempted"


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

    `latency_ms` is the wall-clock latency of the model call -- what the
    design's §6/§7 name and build the overhead metric on -- and is 0.0 when
    nothing measured it (e.g. `parse_reply` called directly, outside a real
    HTTP round trip). `provider_latency_ms` is a provider's own self-reported
    figure if it has one (Ollama's `total_duration` excludes transport time),
    kept alongside rather than instead of the wall-clock figure, and is
    `None` -- never `0.0` -- when the provider did not report one, so an
    absent measurement is never confused with a measurement of zero.
    """

    content: str = ""
    calls: list[ProposedCall] = Field(default_factory=list)
    latency_ms: float = 0.0
    provider_latency_ms: float | None = None
    raw_message: dict = Field(default_factory=dict)


class CallRecord(BaseModel):
    """What happened to one call the model proposed.

    A `CallRecord` may describe a call that never reached the upstream at all
    (`UNKNOWN_TOOL`, `MALFORMED`, `NOT_ATTEMPTED` all mean exactly that) --
    so a consumer counting *executed* calls must filter on `outcome`, never
    on `len(turn.calls)`, which counts every proposal the model made whether
    or not the harness sent it anywhere.
    """

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
        """A header line, then one line per turn.

        The header carries `task_id`, `model`, `truncated` and
        `truncation_reason` -- the fields that live on the Transcript itself,
        not on any one TurnRecord. Without it, a run cut short by a harness
        bound (`max_calls`, `max_model_turns`, a provider error) was
        byte-identical on disk to a run the model finished cleanly: the
        proxy's own `complete` marker is written on clean shutdown regardless
        of whether the *agent* loop was truncated, so this file is the only
        place that bound becomes visible. A header line was chosen over a
        sidecar file so the one artifact stays self-describing and there is
        no second file that can drift out of sync with it; every line,
        header included, is still independently valid JSON, so a reader that
        only wants turns can skip line 1 and keep working.

        Always writes at least the header line, even for a task with zero
        turns -- a run that did nothing must still say, on disk, whether it
        was truncated, rather than reading as a zero-byte file indistinguishable
        from "nothing went wrong."
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "task_id": self.task_id,
            "model": self.model,
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        lines.extend(
            json.dumps(turn.model_dump(), ensure_ascii=False) for turn in self.turns
        )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @classmethod
    def read_jsonl(cls, path) -> Transcript:
        """Reconstruct a `Transcript` from a file `write_jsonl` wrote.

        The first line is the header written above; every line after it is
        one `TurnRecord`.
        """
        path = Path(path)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            raise ValueError(f"{path} has no header line -- not a file write_jsonl wrote")
        header = json.loads(lines[0])
        turns = [TurnRecord.model_validate(json.loads(line)) for line in lines[1:]]
        return cls(
            task_id=header["task_id"],
            model=header["model"],
            turns=turns,
            truncated=header.get("truncated", False),
            truncation_reason=header.get("truncation_reason"),
        )
