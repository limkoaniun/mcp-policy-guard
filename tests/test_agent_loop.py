"""Every outcome class, deterministically, with no model and no proxy.

The real model is an experiment, not a test. These fakes are what make the
loop's classification exhaustively checkable.
"""

import pytest

from enrolment.agent.loop import AgentTask, run_agent
from enrolment.agent.transcript import CallOutcome, ChatReply, ProposedCall, ResponseOutcome


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"does {name}"
        self.input_schema = {"type": "object", "properties": {}}


class FakeToolList:
    def __init__(self, names):
        self.tools = [FakeTool(n) for n in names]


class FakeResult:
    def __init__(self, is_error=False, content="ok"):
        self.is_error = is_error
        self.content = content


class FakeClient:
    """Duck-typed MCP client. Records what it was asked to call."""

    def __init__(self, names=("enrol", "get_subject_details"), errors=()):
        self._names = list(names)
        self._errors = set(errors)
        self.called = []

    async def list_tools(self):
        return FakeToolList(self._names)

    async def call_tool(self, name, arguments):
        self.called.append((name, arguments))
        return FakeResult(is_error=name in self._errors, content=f"result of {name}")


def scripted(*replies):
    """A chat callable returning each reply in turn, then plain text forever."""
    queue = list(replies)

    def chat(messages, tools):
        if queue:
            return queue.pop(0)
        return ChatReply(content="done")

    return chat


def call(name, arguments):
    return ProposedCall(name=name, arguments=arguments, raw={"function": {"name": name}})


def task(turns=("do the thing",)):
    return AgentTask(task_id="T-01", system="You are a test.", turns=list(turns))


@pytest.mark.anyio
async def test_a_forwarded_call_is_recorded_with_its_arguments():
    client = FakeClient()
    chat = scripted(ChatReply(content="", calls=[call("enrol", {"code": "32548"})]))

    transcript = await run_agent(task(), client, chat, model="fake")

    first = transcript.turns[0]
    assert first.outcome is ResponseOutcome.TOOL_CALLS
    assert first.calls[0].outcome is CallOutcome.FORWARDED
    assert first.calls[0].arguments == {"code": "32548"}
    assert client.called == [("enrol", {"code": "32548"})]
    assert transcript.model == "fake"


@pytest.mark.anyio
async def test_an_erroring_call_is_refused_without_attributing_blame():
    """`refused` must not claim the policy did it -- an MCP result cannot tell a
    policy DENY from an upstream error."""
    client = FakeClient(errors=("enrol",))
    chat = scripted(ChatReply(content="", calls=[call("enrol", {"code": "32548"})]))

    transcript = await run_agent(task(), client, chat, model="fake")

    detail = transcript.turns[0].calls[0].detail
    assert transcript.turns[0].calls[0].outcome is CallOutcome.REFUSED
    assert "polic" not in detail.lower(), "detail must not claim a policy did the refusing"


@pytest.mark.anyio
async def test_a_hallucinated_tool_name_is_not_executed():
    client = FakeClient()
    chat = scripted(ChatReply(content="", calls=[call("list_all_majors", {"x": 1})]))

    transcript = await run_agent(task(), client, chat, model="fake")

    assert transcript.turns[0].calls[0].outcome is CallOutcome.UNKNOWN_TOOL
    assert client.called == [], "an unknown tool must never reach the client"


@pytest.mark.anyio
async def test_unparseable_arguments_are_malformed_and_not_executed():
    client = FakeClient()
    chat = scripted(
        ChatReply(content="", calls=[ProposedCall(name="enrol", arguments=None, raw={})])
    )

    transcript = await run_agent(task(), client, chat, model="fake")

    assert transcript.turns[0].calls[0].outcome is CallOutcome.MALFORMED
    assert client.called == []


@pytest.mark.anyio
async def test_plain_text_ends_the_turn():
    client = FakeClient()
    transcript = await run_agent(task(), client, scripted(ChatReply(content="hello")), model="fake")

    assert transcript.turns[0].outcome is ResponseOutcome.TEXT
    assert transcript.turns[0].text == "hello"
    assert client.called == []
    assert transcript.truncated is False, "a normal run must not be reported as truncated"
    assert transcript.truncation_reason is None


@pytest.mark.anyio
async def test_prose_that_looks_like_a_call_is_flagged_by_heuristic():
    client = FakeClient()
    chat = scripted(ChatReply(content='{"name": "list_all_majors", "parameters": {}}'))

    transcript = await run_agent(task(), client, chat, model="fake")

    assert transcript.turns[0].outcome is ResponseOutcome.TEXT_RESEMBLING_A_CALL
    assert client.called == []


@pytest.mark.anyio
async def test_benign_json_prose_without_a_call_sibling_key_is_plain_text():
    """A subject record such as this one also has a "name" key. Without
    requiring a sibling like "arguments" or "parameters", the heuristic would
    misclassify ordinary benign completions as TEXT_RESEMBLING_A_CALL, which
    inflates that class on exactly the benign tasks whose completion rate is
    a headline metric."""
    client = FakeClient()
    chat = scripted(ChatReply(content='{"name": "Data Structures", "code": "31251"}'))

    transcript = await run_agent(task(), client, chat, model="fake")

    assert transcript.turns[0].outcome is ResponseOutcome.TEXT
    assert client.called == []


@pytest.mark.anyio
async def test_calls_and_prose_together_classify_as_calls():
    client = FakeClient()
    chat = scripted(
        ChatReply(content="Let me look that up.", calls=[call("enrol", {"code": "1"})])
    )

    transcript = await run_agent(task(), client, chat, model="fake")

    assert transcript.turns[0].outcome is ResponseOutcome.TOOL_CALLS
    assert transcript.turns[0].text == "Let me look that up."


@pytest.mark.anyio
async def test_each_user_turn_gets_its_own_records():
    """The turn script is the whole point: a second user turn is what gives the
    model a decision point after a poisoned tool result has landed."""
    client = FakeClient()
    chat = scripted(
        ChatReply(content="", calls=[call("get_subject_details", {"code": "32541"})]),
        ChatReply(content="read it"),
        ChatReply(content="", calls=[call("enrol", {"code": "32541"})]),
        ChatReply(content="done"),
    )

    transcript = await run_agent(
        task(turns=("what is 32541?", "go ahead")), client, chat, model="fake"
    )

    assert {t.user_turn for t in transcript.turns} == {0, 1}
    assert [c[0] for c in client.called] == ["get_subject_details", "enrol"]
    assert len(transcript.turns) == 4, "a set of user_turn values hides a duplicated/dropped record"


@pytest.mark.anyio
async def test_hitting_the_call_budget_marks_the_run_truncated():
    client = FakeClient()
    chat = scripted(*[ChatReply(content="", calls=[call("enrol", {"n": i})]) for i in range(10)])

    transcript = await run_agent(task(), client, chat, model="fake", max_calls=3)

    assert transcript.truncated is True
    assert "max_calls" in transcript.truncation_reason
    assert len(client.called) == 3


@pytest.mark.anyio
async def test_the_call_budget_records_proposals_it_swallows():
    """A proposal the budget swallows must not vanish -- silently dropping it
    would make a proposed attack indistinguishable from one never attempted,
    and no later subsystem can recover evidence that was never written."""
    client = FakeClient()
    chat = scripted(
        ChatReply(
            content="",
            calls=[
                call("enrol", {"code": "1"}),
                call("enrol", {"code": "2"}),
                call("enrol", {"code": "3"}),
            ],
        )
    )

    transcript = await run_agent(task(), client, chat, model="fake", max_calls=1)

    calls = transcript.turns[0].calls
    assert len(calls) == 3
    assert calls[0].outcome is CallOutcome.FORWARDED
    assert calls[1].outcome is CallOutcome.NOT_ATTEMPTED
    assert calls[2].outcome is CallOutcome.NOT_ATTEMPTED
    assert len(client.called) == 1


@pytest.mark.anyio
async def test_a_swallowed_proposal_that_is_itself_malformed_stays_malformed():
    """A genuine model defect must not be laundered into NOT_ATTEMPTED just
    because the budget also ran out -- MALFORMED measures the model, and
    detecting unreadable shape costs nothing against the budget."""
    client = FakeClient()
    chat = scripted(
        ChatReply(
            content="",
            calls=[
                call("enrol", {"code": "1"}),
                ProposedCall(name="enrol", arguments=None, raw={}),
            ],
        )
    )

    transcript = await run_agent(task(), client, chat, model="fake", max_calls=1)

    calls = transcript.turns[0].calls
    assert calls[0].outcome is CallOutcome.FORWARDED
    assert calls[1].outcome is CallOutcome.MALFORMED
    assert len(client.called) == 1


@pytest.mark.anyio
async def test_hitting_the_model_turn_budget_marks_the_run_truncated():
    client = FakeClient()
    chat = scripted(*[ChatReply(content="", calls=[call("enrol", {"n": i})]) for i in range(10)])

    transcript = await run_agent(task(), client, chat, model="fake", max_model_turns=2)

    assert transcript.truncated is True
    assert "max_model_turns" in transcript.truncation_reason


@pytest.mark.anyio
async def test_the_loop_never_repairs_a_malformed_call():
    """Repairing the model's mistakes would make the harness a participant in
    what it measures."""
    client = FakeClient()
    chat = scripted(
        ChatReply(content="", calls=[ProposedCall(name="enrol", arguments=None, raw={})]),
        ChatReply(content="finished"),
    )

    transcript = await run_agent(task(), client, chat, model="fake")

    assert client.called == []
    assert transcript.turns[-1].outcome is ResponseOutcome.TEXT


@pytest.mark.anyio
async def test_a_provider_error_mid_run_preserves_the_turns_already_recorded():
    """The caller writes the transcript only after run_agent returns, so a
    daemon hiccup after some turns already succeeded must not lose them."""
    client = FakeClient()
    seen = []

    def flaky_chat(messages, tools):
        seen.append(1)
        if len(seen) == 1:
            return ChatReply(content="first turn ok")
        raise TimeoutError("daemon did not respond")

    transcript = await run_agent(
        task(turns=("first", "second")), client, flaky_chat, model="fake"
    )

    assert len(transcript.turns) == 2
    assert transcript.turns[0].outcome is ResponseOutcome.TEXT
    assert transcript.turns[0].text == "first turn ok"
    assert transcript.turns[1].outcome is ResponseOutcome.PROVIDER_ERROR
    assert "TimeoutError" in transcript.turns[1].text
    assert transcript.truncated is True
    assert "TimeoutError" in transcript.truncation_reason
