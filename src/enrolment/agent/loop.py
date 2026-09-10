"""Drive a model through an MCP conversation and record what it did.

The model arrives as a callable rather than an import, so every outcome class
is reachable in a test with no daemon running. The MCP client is duck-typed:
anything satisfying this shape works, which lets the tests use a fake and the
experiments use the real proxy client --

- async `list_tools()` returning an object with a `.tools` list, each tool
  carrying `.name`, `.description` and `.input_schema`;
- async `call_tool(name, arguments)` returning an object carrying `.is_error`
  and `.content`.

Two things this loop deliberately does not do. It does not repair the model's
mistakes -- a malformed call is recorded and the conversation continues,
because repairing them would make the harness a participant in what it
measures. And it does not judge: `refused` means the call returned an error,
not that a policy refused it, which an MCP result cannot say.

A third thing it does not do: retry. A chat callable that raises (a daemon
timeout, a connection error, an unparseable body) is recorded as a
PROVIDER_ERROR turn and the run ends there, returning every turn already
gathered -- not swallowed, and not retried in place, because a retry the
harness performed silently on the model's behalf is exactly the kind of
repair this loop refuses to do everywhere else.
"""

# NOTE on CallOutcome for budget-swallowed proposals: when `max_calls` trips
# partway through a reply's call list, the remaining proposals are recorded
# rather than left unrecorded, using CallOutcome.NOT_ATTEMPTED -- a proposal
# the harness never sent because a harness-side bound was reached first, not
# because the model did anything wrong. It must never be folded into
# MALFORMED or UNKNOWN_TOOL: those measure a genuine property of the model's
# output (how often it emits garbage, or hallucinates a tool), and reported
# as a result, so they must stay uncontaminated by our own bookkeeping.
#
# One ordering choice this forces: a swallowed proposal that is *itself*
# unreadable (no name, unparseable arguments) or names an unknown tool is
# still classified MALFORMED / UNKNOWN_TOOL, never NOT_ATTEMPTED. Checking
# shape and tool identity costs nothing against the budget -- it never
# touches `calls_made` or the client -- so there is no reason to hide a real
# model defect behind the harness's bookkeeping just because the harness also
# ran out of room to execute it. NOT_ATTEMPTED is reserved for proposals that
# were otherwise entirely fine.

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

    Requiring a sibling `arguments`/`parameters`/`tool` key alongside "name"
    (see `_CALL_SIBLING_KEYS`) means a hallucinated *zero-argument* call --
    `{"name": "list_all_majors"}` with nothing beside it -- reads as plain
    TEXT, not TEXT_RESEMBLING_A_CALL. That is a deliberate trade: it
    under-reports this class rather than risk inflating it onto benign JSON
    prose that also happens to carry a "name" key, and under-reporting a rare
    edge case is the safer failure than corrupting a headline completion-rate
    metric on ordinary benign tasks.
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


def _bounded(text: str, limit: int) -> str:
    """Cut `text` to `limit` characters, marking the cut so a reader can tell
    a short result from a clipped one.

    Follows the `[truncated]` convention already used elsewhere in this
    project (see `_describe` in `src/enrolment/requisites.py`) rather than
    inventing a second one.
    """
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " [truncated]"


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
    history: list[dict] | None = None,
    on_event: Callable[[dict], None] | None = None,
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
    messages: list[dict] = history if history is not None else []
    if task.system and not any(m.get("role") == "system" for m in messages):
        messages.append({"role": "system", "content": task.system})

    def emit(**event) -> None:
        if on_event is not None:
            on_event(event)

    calls_made = 0

    for user_turn, text in enumerate(task.turns):
        messages.append({"role": "user", "content": text})
        emit(kind="user", text=text)

        for model_turn in range(max_model_turns):
            try:
                reply = chat(messages, payload)
            except Exception as exc:  # noqa: BLE001 - any provider failure ends the run, recorded
                # `_post` (or whatever the chat callable does under the hood)
                # can still raise -- timeout, connection error, a body that
                # isn't JSON. `run_agent` is the last point that can catch
                # this before the caller writes the transcript, and it must:
                # every turn already recorded here is otherwise lost even
                # though it happened. Not retried -- see the module docstring.
                failure = f"{type(exc).__name__}: {exc}"
                transcript.turns.append(
                    TurnRecord(
                        user_turn=user_turn,
                        model_turn=model_turn,
                        outcome=ResponseOutcome.PROVIDER_ERROR,
                        text=failure,
                    )
                )
                transcript.truncated = True
                transcript.truncation_reason = (
                    f"provider error on user turn {user_turn}: {failure}"
                )
                return transcript

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
                emit(kind="said", text=reply.content, outcome=outcome.value)
                break

            messages.append(reply.raw_message or {"role": "assistant", "content": reply.content})

            for index, proposed in enumerate(reply.calls):
                if calls_made >= max_calls:
                    # The budget tripped before this proposal (and any after
                    # it in this reply) could even be sent. Record each one
                    # rather than dropping it silently -- a proposal the
                    # budget swallowed must not become indistinguishable from
                    # an attack that was never attempted. See the module-level
                    # NOTE above: a genuine model defect (unreadable shape, or
                    # an unknown tool) still wins over NOT_ATTEMPTED, since
                    # detecting it costs nothing against the budget.
                    for swallowed in reply.calls[index:]:
                        if swallowed.name is None or not isinstance(swallowed.arguments, dict):
                            record.calls.append(
                                CallRecord(
                                    tool=swallowed.name or "",
                                    outcome=CallOutcome.MALFORMED,
                                    detail=(
                                        "the model's call could not be read as a name "
                                        "and arguments"
                                    ),
                                )
                            )
                            continue

                        if swallowed.name not in known:
                            record.calls.append(
                                CallRecord(
                                    tool=swallowed.name,
                                    arguments=swallowed.arguments,
                                    outcome=CallOutcome.UNKNOWN_TOOL,
                                    detail="no such tool is exposed by the upstream",
                                )
                            )
                            continue

                        record.calls.append(
                            CallRecord(
                                tool=swallowed.name,
                                arguments=swallowed.arguments,
                                outcome=CallOutcome.NOT_ATTEMPTED,
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
                        detail=_bounded(body, 500),
                    )
                )
                emit(
                    kind="call",
                    tool=proposed.name,
                    arguments=proposed.arguments,
                    refused=bool(result.is_error),
                    detail=_bounded(body, 500),
                )
                messages.append(_tool_message(_bounded(body, 2000), proposed.call_id))

            transcript.turns.append(record)

            if model_turn == max_model_turns - 1:
                transcript.truncated = True
                transcript.truncation_reason = (
                    f"max_model_turns ({max_model_turns}) reached on user turn {user_turn}"
                )
                return transcript

    return transcript
