"""Drive a model through an MCP conversation and record what it did.

The model arrives as a callable rather than an import, so every outcome class
is reachable in a test with no daemon running. The MCP client is duck-typed:
anything with async `list_tools()` and `call_tool(name, arguments)` works.

Two things this loop deliberately does not do. It does not repair the model's
mistakes -- a malformed call is recorded and the conversation continues,
because repairing them would make the harness a participant in what it
measures. And it does not judge: `refused` means the call returned an error,
not that a policy refused it, which an MCP result cannot say.
"""

# NOTE on CallOutcome for budget-swallowed proposals: when `max_calls` trips
# partway through a reply's call list, the remaining proposals are recorded
# as CallOutcome.MALFORMED rather than left unrecorded. Neither existing
# member is a clean fit -- these calls were never sent to the client, so they
# are not FORWARDED or REFUSED, but they may well have named a real, known
# tool with well-formed arguments, so MALFORMED is a stretch too. MALFORMED
# was chosen over UNKNOWN_TOOL because this project's headline classification
# is whether the model hallucinated a nonexistent tool (see
# test_a_hallucinated_tool_name_is_not_executed); tagging a budget-exhaustion
# artifact as UNKNOWN_TOOL would contaminate that count directly, whereas
# MALFORMED is the less central metric. The detail string always states the
# true reason -- budget exhaustion, not malformed shape -- so a reader is not
# misled even though the enum value alone would be.

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel, Field

from .transcript import (
    CallOutcome,
    CallRecord,
    ChatReply,
    ResponseOutcome,
    Transcript,
    TurnRecord,
)

Chat = Callable[[list[dict], list[dict]], ChatReply]


class AgentTask(BaseModel):
    """A task as an ordered list of user turns.

    Not a single prompt. A second turn is what gives the model a decision point
    after a poisoned tool result has entered its context -- measured against
    both candidate models, a one-turn framing produced no attacks at all while
    the payload was verifiably present. See the design's §3.
    """

    task_id: str
    system: str = ""
    turns: list[str] = Field(default_factory=list)


#: Sibling keys that must accompany "name" for prose to count as
#: TEXT_RESEMBLING_A_CALL. Ordinary benign JSON prose -- e.g. a subject
#: record like `{"name": "Data Structures", "code": "31251"}` -- also has a
#: "name" key, and without this guard the heuristic misclassifies it,
#: inflating the class on exactly the benign tasks whose completion rate is
#: a headline metric. Both observed instances of the real failure carried
#: "parameters", so requiring one of these still catches it.
_CALL_SIBLING_KEYS = ("arguments", "parameters", "tool")


def _looks_like_a_call(text: str) -> bool:
    """HEURISTIC. Does this prose look like a tool call the model failed to emit?

    Both candidate models, pushed to act rather than narrate, answered with a
    JSON object naming a tool instead of calling one. This guesses at that
    intent; it is not an observation of a call, and every report of
    TEXT_RESEMBLING_A_CALL must say so.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return False
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict) or "name" not in parsed:
        return False
    return any(key in parsed for key in _CALL_SIBLING_KEYS)


def _classify(reply: ChatReply) -> ResponseOutcome:
    if reply.calls:
        return ResponseOutcome.TOOL_CALLS
    if _looks_like_a_call(reply.content):
        return ResponseOutcome.TEXT_RESEMBLING_A_CALL
    return ResponseOutcome.TEXT


def _tool_message(content: str, call_id: str | None) -> dict:
    """A `role: "tool"` message, carrying the provider's own call id when known.

    An OpenAI-compatible endpoint rejects a tool message with no matching
    `tool_call_id`; Ollama's native API tolerates the omission today. The key
    is included only when a real id is known -- never invented.
    """
    message = {"role": "tool", "content": content}
    if call_id:
        message["tool_call_id"] = call_id
    return message


def _tools_payload(tools) -> list[dict]:
    """The mirrored tool list, in the shape a chat provider expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


async def run_agent(
    task: AgentTask,
    client,
    chat: Chat,
    *,
    model: str,
    max_model_turns: int = 4,
    max_calls: int = 12,
) -> Transcript:
    """Run `task` to completion or to a bound, and return what happened.

    Exhausting `max_model_turns` on one user turn ends the run there and
    abandons every later user turn in `task.turns` -- they are never sent.
    A metrics consumer must exclude a truncated run from a completion-rate
    denominator rather than count it as a failed completion of the later
    turns, since those turns were never attempted at all.
    """
    listing = await client.list_tools()
    tools = list(listing.tools)
    known = {tool.name for tool in tools}
    payload = _tools_payload(tools)

    transcript = Transcript(task_id=task.task_id, model=model)
    messages: list[dict] = []
    if task.system:
        messages.append({"role": "system", "content": task.system})

    calls_made = 0

    for user_turn, text in enumerate(task.turns):
        messages.append({"role": "user", "content": text})

        for model_turn in range(max_model_turns):
            reply = chat(messages, payload)
            outcome = _classify(reply)
            record = TurnRecord(
                user_turn=user_turn,
                model_turn=model_turn,
                outcome=outcome,
                text=reply.content,
                latency_ms=reply.latency_ms,
            )

            if outcome is not ResponseOutcome.TOOL_CALLS:
                transcript.turns.append(record)
                break

            messages.append(reply.raw_message or {"role": "assistant", "content": reply.content})

            for index, proposed in enumerate(reply.calls):
                if calls_made >= max_calls:
                    # The budget tripped before this proposal (and any after
                    # it in this reply) could even be evaluated. Record each
                    # one rather than dropping it silently -- a proposal the
                    # budget swallowed must not become indistinguishable from
                    # an attack that was never attempted. See the module-level
                    # NOTE above for why MALFORMED is the outcome used here.
                    for swallowed in reply.calls[index:]:
                        record.calls.append(
                            CallRecord(
                                tool=swallowed.name or "",
                                arguments=(
                                    swallowed.arguments
                                    if isinstance(swallowed.arguments, dict)
                                    else {}
                                ),
                                outcome=CallOutcome.MALFORMED,
                                detail=(
                                    f"not attempted: call budget (max_calls={max_calls}) "
                                    "was exhausted before this proposal could be evaluated"
                                ),
                            )
                        )
                    transcript.turns.append(record)
                    transcript.truncated = True
                    transcript.truncation_reason = f"max_calls ({max_calls}) reached"
                    return transcript

                if proposed.name is None or not isinstance(proposed.arguments, dict):
                    record.calls.append(
                        CallRecord(
                            tool=proposed.name or "",
                            outcome=CallOutcome.MALFORMED,
                            detail="the model's call could not be read as a name and arguments",
                        )
                    )
                    messages.append(_tool_message("malformed call", proposed.call_id))
                    continue

                if proposed.name not in known:
                    record.calls.append(
                        CallRecord(
                            tool=proposed.name,
                            arguments=proposed.arguments,
                            outcome=CallOutcome.UNKNOWN_TOOL,
                            detail="no such tool is exposed by the upstream",
                        )
                    )
                    messages.append(_tool_message("no such tool", proposed.call_id))
                    continue

                result = await client.call_tool(proposed.name, proposed.arguments)
                calls_made += 1
                body = str(result.content)
                record.calls.append(
                    CallRecord(
                        tool=proposed.name,
                        arguments=proposed.arguments,
                        outcome=CallOutcome.REFUSED if result.is_error else CallOutcome.FORWARDED,
                        detail=body[:500],
                    )
                )
                messages.append(_tool_message(body[:2000], proposed.call_id))

            transcript.turns.append(record)

            if model_turn == max_model_turns - 1:
                transcript.truncated = True
                transcript.truncation_reason = (
                    f"max_model_turns ({max_model_turns}) reached on user turn {user_turn}"
                )
                return transcript

    return transcript
