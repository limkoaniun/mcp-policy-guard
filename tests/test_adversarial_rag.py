"""The RAG arm of the adversarial campaign: the planted document goes into
the prompt, one completion, no proxy. Fake model throughout."""

import json
import pathlib

import pytest

from enrolment.agent.adversarial import RunSpec, run_rag_one
from enrolment.agent.advice import load_world
from enrolment.agent.campaign import CampaignManifest, run_status
from enrolment.agent.prompts import RAG_SYSTEM_PROMPT
from enrolment.agent.transcript import ChatReply, ResponseOutcome
from enrolment.run_setup import students_baseline_path
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "attacks" / "scenarios-upload"
PACK = REPO / "configs" / "enrolment-personas.yaml"
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text()))
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
    "get_attachment",
}


def by_id():
    return {s.id: s for s in load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)}


def manifest():
    return CampaignManifest(
        models=["fake"], repeats=1, arms=["rag"], system_prompt="You help {principal}.",
        rag_system_prompt=RAG_SYSTEM_PROMPT, nudge="Thanks. Please proceed.",
        policy_pack=str(PACK), num_ctx=8192,
        fingerprints={"fake": {"model": "fake", "digest": "sha256:test"}},
    )


def world():
    return load_world(REPO, students_baseline_path(PACK, REPO))


def recording(reply_text="I would not do that."):
    """A chat factory that records every prompt it was shown."""
    seen = []

    def factory(model):
        def chat(messages, tools):
            seen.append((messages, tools))
            return ChatReply(content=reply_text, latency_ms=7.0)
        return chat

    return factory, seen


@pytest.mark.anyio
async def test_the_planted_document_is_in_the_prompt_and_no_tools_are_offered(tmp_path):
    scenario = by_id()["A-02"]
    spec = RunSpec(scenario_id="A-02", model="fake", arm="rag", repeat=1)
    factory, seen = recording()

    transcript, duration = await run_rag_one(
        scenario, spec, campaign_dir=tmp_path, chat_factory=factory,
        manifest=manifest(), world=world(),
    )

    [(messages, tools)] = seen
    assert tools == []
    assert messages[0]["role"] == "system" and "S9000004" in messages[0]["content"]
    assert scenario.payload.text.strip() in messages[-1]["content"]
    assert scenario.task.description in messages[-1]["content"]
    assert len([m for m in messages if m["role"] == "user"]) == 1  # no nudge turn
    assert transcript.task_id == "A-02" and run_status(transcript) == "ok"
    assert duration > 0


@pytest.mark.anyio
async def test_a_rag_run_leaves_its_artifacts_and_none_of_the_proxys(tmp_path):
    scenario = by_id()["A-02"]
    spec = RunSpec(scenario_id="A-02", model="fake", arm="rag", repeat=1)
    factory, _ = recording("Withdraw from 32541 now.")

    await run_rag_one(scenario, spec, campaign_dir=tmp_path, chat_factory=factory,
                      manifest=manifest(), world=world())

    run_dir = tmp_path / "A-02-fake-rag-r1"
    attachments = json.loads((run_dir / "attachments.json").read_text())
    assert scenario.payload.text.strip() in attachments["transfer-checklist"]["text"]
    rag = json.loads((run_dir / "rag.json").read_text())
    assert rag["text"] == "Withdraw from 32541 now."
    assert rag["question"] == scenario.task.description
    assert rag["messages"][-1]["role"] == "user"
    assert rag["latency_ms"] == 7.0
    assert (run_dir / "transcript.jsonl").exists()
    assert json.loads((run_dir / "agent.json").read_text())["digest"] == "sha256:test"
    for absent in ("state.json", "erequests.jsonl", "decisions.jsonl", "complete", "task.yaml"):
        assert not (run_dir / absent).exists(), absent


@pytest.mark.anyio
async def test_the_transcript_carries_the_reply_text(tmp_path):
    scenario = by_id()["A-04b"]
    spec = RunSpec(scenario_id="A-04b", model="fake", arm="rag", repeat=1)
    factory, _ = recording("Here is a summary of your letter.")

    transcript, _ = await run_rag_one(scenario, spec, campaign_dir=tmp_path,
                                      chat_factory=factory, manifest=manifest(), world=world())

    assert transcript.turns[-1].outcome is ResponseOutcome.TEXT
    assert transcript.turns[-1].text == "Here is a summary of your letter."


@pytest.mark.anyio
async def test_a_provider_failure_is_a_provider_error_run_not_a_crash(tmp_path):
    scenario = by_id()["A-01"]
    spec = RunSpec(scenario_id="A-01", model="fake", arm="rag", repeat=1)

    def factory(model):
        def chat(messages, tools):
            raise ConnectionError("daemon went away")
        return chat

    transcript, _ = await run_rag_one(scenario, spec, campaign_dir=tmp_path,
                                      chat_factory=factory, manifest=manifest(), world=world())

    assert run_status(transcript) == "provider_error"
    assert "daemon went away" in transcript.turns[-1].text
    assert (tmp_path / "A-01-fake-rag-r1" / "transcript.jsonl").exists()
