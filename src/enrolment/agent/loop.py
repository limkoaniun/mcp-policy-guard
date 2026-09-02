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
    return isinstance(parsed, dict) and "name" in parsed


def _classify(reply: ChatReply) -> ResponseOutcome:
    if reply.calls:
        return ResponseOutcome.TOOL_CALLS
    if _looks_like_a_call(reply.content):
        return ResponseOutcome.TEXT_RESEMBLING_A_CALL
    return ResponseOutcome.TEXT


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
    """Run `task` to completion or to a bound, and return what happened."""
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

            for proposed in reply.calls:
                if calls_made >= max_calls:
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
                    messages.append({"role": "tool", "content": "malformed call"})
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
                    messages.append({"role": "tool", "content": "no such tool"})
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
                messages.append({"role": "tool", "content": body[:2000]})

            transcript.turns.append(record)

            if model_turn == max_model_turns - 1:
                transcript.truncated = True
                transcript.truncation_reason = (
                    f"max_model_turns ({max_model_turns}) reached on user turn {user_turn}"
                )
                return transcript

    return transcript
