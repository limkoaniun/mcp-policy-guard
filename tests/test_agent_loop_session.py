"""Two additions the demo's chat session needs from the agent loop.

Both are additive and default to off, because the campaigns must keep running
the loop they measured. A demo that drove a second, near-identical loop would
be free to drift from the code that produced the results.
"""

import pytest
from test_agent_loop import FakeClient, call, scripted, task

from enrolment.agent.loop import run_agent
from enrolment.agent.transcript import ChatReply


@pytest.mark.anyio
async def test_history_carries_a_conversation_across_calls():
    """A chat session is many turns against one context. `history` is the
    caller's own message list, mutated in place, so the next message continues
    the conversation rather than starting one."""
    history = []
    client = FakeClient()

    await run_agent(task(turns=["first"]), client, scripted(ChatReply(content="one")),
                    model="fake", history=history)
    await run_agent(task(turns=["second"]), client, scripted(ChatReply(content="two")),
                    model="fake", history=history)

    said = [m for m in history if m.get("role") == "user"]
    assert [m["content"] for m in said] == ["first", "second"]


@pytest.mark.anyio
async def test_the_system_prompt_is_set_once_not_once_per_message():
    history = []
    client = FakeClient()

    for turn in ("first", "second"):
        await run_agent(task(turns=[turn]), client, scripted(ChatReply(content="ok")),
                        model="fake", history=history)

    assert [m["role"] for m in history].count("system") == 1


@pytest.mark.anyio
async def test_events_are_emitted_as_they_happen():
    """The page renders the run as it unfolds. Emitting only at the end would
    leave a viewer watching a spinner for the length of a model call."""
    events = []
    client = FakeClient()
    chat = scripted(ChatReply(content="", calls=[call("enrol", {"code": "32548"})]))

    await run_agent(task(), client, chat, model="fake", on_event=events.append)

    kinds = [e["kind"] for e in events]
    assert "call" in kinds
    assert kinds.index("call") < len(kinds) - 1, "a call must be emitted before the run ends"
    assert any(e.get("tool") == "enrol" for e in events if e["kind"] == "call")
