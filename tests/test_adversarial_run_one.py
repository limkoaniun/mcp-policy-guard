"""One run: real proxy, real dataset, fake model.

The proxy subprocess is real because the run directory it writes is the whole
point of this subsystem. The model is fake because a real one makes the test
slow, nondeterministic and skippable -- A's §5 discipline, applied here.
"""

import json
import pathlib

import pytest

from enrolment.agent.adversarial import RunSpec, run_one
from enrolment.agent.campaign import CampaignManifest, run_status
from enrolment.agent.transcript import ChatReply, ProposedCall, Transcript
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
SCENARIOS = REPO / "attacks" / "scenarios"
PACK = REPO / "configs" / "enrolment-personas.yaml"
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text()))
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
}


def by_id():
    return {s.id: s for s in load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)}


def manifest():
    return CampaignManifest(
        models=["fake"], repeats=1, system_prompt="You help {principal}.",
        nudge="Thanks. Please proceed.", policy_pack=str(PACK),
        fingerprints={"fake": {"model": "fake", "digest": "sha256:test"}},
    )


def scripted(*replies):
    """A chat factory whose model returns each reply in turn, then plain text."""
    def factory(model):
        queue = list(replies)

        def chat(messages, tools):
            return queue.pop(0) if queue else ChatReply(content="done")

        return chat

    return factory


def call(name, arguments):
    return ProposedCall(name=name, arguments=arguments, raw={"function": {"name": name}})


@pytest.mark.anyio
async def test_a_run_leaves_every_artifact_the_metrics_subsystem_reads(tmp_path):
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)

    transcript, duration = await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(
            ChatReply(content="", calls=[call("get_subject_details", {"code": "42027"})])
        ),
        manifest=manifest(),
    )

    run_dir = tmp_path / spec.run_id
    assert (run_dir / "transcript.jsonl").exists()
    assert (run_dir / "decisions.jsonl").exists()
    assert (run_dir / "state.json").exists()
    assert (run_dir / "agent.json").exists()
    assert (run_dir / "task.yaml").exists()
    assert transcript.task_id == "SC-07"
    assert duration > 0


@pytest.mark.anyio
async def test_a_deliverable_payload_is_delivered_on_the_on_arm_too(tmp_path):
    """An enforcement-on run of an injected scenario is only evidence if the
    injection actually reached the model."""
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)

    await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(), manifest=manifest(),
    )

    assert (tmp_path / spec.run_id / "overlay.yaml").exists()


@pytest.mark.anyio
async def test_an_undeliverable_scenario_writes_no_overlay(tmp_path):
    scenario = by_id()["SC-01"]
    spec = RunSpec(scenario_id="SC-01", model="fake", arm="on", repeat=1)

    await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(), manifest=manifest(),
    )

    assert not (tmp_path / spec.run_id / "overlay.yaml").exists()


@pytest.mark.anyio
async def test_agent_json_names_the_weights_that_produced_the_run(tmp_path):
    """Spec §7: a number without the weights behind it is not reproducible."""
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)

    await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(), manifest=manifest(),
    )

    provenance = json.loads((tmp_path / spec.run_id / "agent.json").read_text())
    assert provenance["digest"] == "sha256:test"


@pytest.mark.anyio
async def test_a_provider_failure_still_leaves_a_readable_transcript(tmp_path):
    """Spec §8: the campaign decides whether to replace this run by reading
    what it left behind, so the failure must survive on disk."""
    def exploding(model):
        def chat(messages, tools):
            raise TimeoutError("timed out")
        return chat

    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)

    transcript, _ = await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=exploding, manifest=manifest(),
    )

    assert run_status(transcript) == "provider_error"
    assert (tmp_path / spec.run_id / "transcript.jsonl").exists()


@pytest.mark.anyio
async def test_a_directory_holding_a_finished_run_still_fails_loudly(tmp_path):
    """`RunState` raises rather than resetting state.json under a live
    decisions.jsonl. B must not paper over that.

    A readable transcript means the run finished, so a runner being asked to
    make it again can only mean the resume logic believed the directory was
    fresh when it was not -- and that must surface, not be tidied away.
    """
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)
    run_dir = tmp_path / spec.run_id
    run_dir.mkdir(parents=True)
    Transcript(task_id="SC-07", model="fake").write_jsonl(run_dir / "transcript.jsonl")

    with pytest.raises(FileExistsError) as error:
        await run_one(
            scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
            chat_factory=scripted(), manifest=manifest(),
        )

    assert spec.run_id in str(error.value)
    # Nothing was moved: a finished run is evidence, and so is where it sits.
    assert [p.name for p in tmp_path.iterdir()] == [spec.run_id]


def _interrupted(run_dir):
    """What killing a campaign mid-run actually leaves behind.

    `run_one` makes the directory first and writes `transcript.jsonl` last,
    after the proxy subprocess has shut down, so everything except the
    transcript is already there.
    """
    run_dir.mkdir(parents=True)
    (run_dir / "decisions.jsonl").write_text('{"tool": "enrol"}\n', encoding="utf-8")
    (run_dir / "state.json").write_text("{}", encoding="utf-8")


@pytest.mark.anyio
async def test_an_interrupted_run_directory_is_moved_aside_not_fatal(tmp_path, capsys):
    """Design §9 promises resume "continues where it stopped". A 280-run
    campaign is overwhelmingly likely to be stopped mid-run, which leaves a
    directory with no transcript in it -- and that used to kill the resume
    with a bare FileExistsError, no explanation and no remedy."""
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)
    _interrupted(tmp_path / spec.run_id)

    transcript, _ = await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(), manifest=manifest(),
    )

    assert run_status(transcript) == "ok"
    assert (tmp_path / spec.run_id / "transcript.jsonl").exists()
    # Moved, never deleted: the partial decisions.jsonl is evidence.
    kept = tmp_path / f"{spec.run_id}.interrupted-1"
    assert kept.is_dir()
    assert (kept / "decisions.jsonl").read_text() == '{"tool": "enrol"}\n'
    assert kept.name in capsys.readouterr().out


@pytest.mark.anyio
async def test_a_second_interruption_does_not_overwrite_the_first(tmp_path, capsys):
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)
    (tmp_path / f"{spec.run_id}.interrupted-1").mkdir(parents=True)
    _interrupted(tmp_path / spec.run_id)

    await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(), manifest=manifest(),
    )

    assert (tmp_path / f"{spec.run_id}.interrupted-2" / "decisions.jsonl").exists()
    assert list((tmp_path / f"{spec.run_id}.interrupted-1").iterdir()) == []


@pytest.mark.anyio
async def test_an_unreadable_transcript_is_treated_as_an_interruption(tmp_path):
    """`existing_status` already reads a half-written transcript as "not run",
    so resume asks for it again. The two must agree, or resume would ask for a
    run that `run_one` then refuses to make."""
    scenario = by_id()["SC-07"]
    spec = RunSpec(scenario_id="SC-07", model="fake", arm="on", repeat=1)
    _interrupted(tmp_path / spec.run_id)
    (tmp_path / spec.run_id / "transcript.jsonl").write_text("{half a li", encoding="utf-8")

    transcript, _ = await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=scripted(), manifest=manifest(),
    )

    assert run_status(transcript) == "ok"
    assert (tmp_path / f"{spec.run_id}.interrupted-1").is_dir()
