"""The advice campaign: two arms on the same questions, no daemon needed."""

import json
import pathlib

import pytest
import yaml

from enrolment.advice import AdviceTask
from enrolment.agent import advice as cli
from enrolment.agent.campaign import CampaignManifest, RunSpec, existing_status, run_status
from enrolment.agent.transcript import ChatReply, ProposedCall, ResponseOutcome, Transcript

REPO = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO / "configs" / "enrolment.yaml"


def elig():
    return AdviceTask(
        task_id="E-S1000001-32933", kind="eligibility", student_id="S1000001",
        question="Am I eligible to enrol in 32933 Research Project next session? Begin your reply with exactly one of the words ELIGIBLE or NOT ELIGIBLE, then explain.",
        expected_eligible=False, subject_code="32933", rule_depth=3, stratum="core",
    )


def manifest():
    return CampaignManifest(
        models=["fake"], repeats=1, system_prompt="You help {principal}.",
        nudge="Thanks. Please proceed.", policy_pack=str(PACK),
        fingerprints={"fake": {"model": "fake"}}, sample_digest="abc",
    )


def scripted(*replies):
    def factory(model):
        queue = list(replies)

        def chat(messages, tools):
            return queue.pop(0) if queue else ChatReply(content="NOT ELIGIBLE. Missing 32144.")

        return chat

    return factory


def test_the_defaults_match_the_compliance_pair_and_the_design():
    args = cli.parse_args(["--campaign", "x"])
    assert args.models == ["llama3.1:8b", "llama3.2"]
    assert args.repeats == 3
    assert args.kinds == ["eligibility", "session"]
    assert args.arms == ["rag", "agent"]
    assert args.sample_seed == 20260903


def test_the_matrix_is_tasks_by_models_by_repeats_by_arms():
    tasks = [elig()]
    specs = cli.enumerate_matrix(tasks, ["a", "b"], 3, ["rag", "agent"])
    assert len(specs) == 12
    assert {s.arm for s in specs} == {"rag", "agent"}
    assert specs[0].repeat == 1 and specs[-1].repeat == 3, "repeat is the outermost loop"


def test_a_dry_run_reports_the_default_size(capsys):
    assert cli.main(["--campaign", "x", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "348" in printed and "4176" in printed


def test_kinds_narrow_the_task_set(capsys):
    cli.main(["--campaign", "x", "--dry-run", "--kinds", "eligibility"])
    printed = capsys.readouterr().out
    assert "248" in printed and "2976" in printed


def test_the_agent_task_is_one_turn_with_no_nudge():
    task = cli.task_for(elig(), "Help {principal}.")
    assert task.turns == [elig().question]
    assert task.system == "Help S1000001."


def test_the_read_only_allowlist_excludes_every_acting_tool():
    assert set(cli.ADVICE_TOOLS) & {"enrol", "drop", "submit_erequest"} == set()
    assert {"check_prerequisites", "get_session_options", "get_student_record"} <= set(cli.ADVICE_TOOLS)


@pytest.mark.anyio
async def test_the_rag_arm_writes_rag_json_and_a_readable_one_turn_transcript(tmp_path):
    world = cli.load_world(REPO, REPO / "data" / "students.json")
    spec = RunSpec(scenario_id="E-S1000001-32933", model="fake", arm="rag", repeat=1)
    transcript, duration = await cli.run_rag_one(
        elig(), spec, campaign_dir=tmp_path, chat_factory=scripted(), manifest=manifest(), world=world,
    )
    run_dir = tmp_path / spec.run_id
    assert run_status(transcript) == "ok"
    assert existing_status(run_dir) == "ok"
    body = json.loads((run_dir / "rag.json").read_text())
    assert body["text"].startswith("NOT ELIGIBLE") and "Subject 32933" in "\n".join(body["context"])
    read = Transcript.read_jsonl(run_dir / "transcript.jsonl")
    assert len(read.turns) == 1 and read.turns[0].outcome is ResponseOutcome.TEXT
    assert read.turns[0].text.startswith("NOT ELIGIBLE")
    assert duration >= 0


@pytest.mark.anyio
async def test_the_agent_arm_runs_through_the_real_proxy_read_only(tmp_path):
    """The one subprocess test: the guarded agent answers with tools, and the
    allowlist holds — enrol is not even declared for the task."""
    spec = RunSpec(scenario_id="E-S1000001-32933", model="fake", arm="agent", repeat=1)
    chat = scripted(
        ChatReply(content="", calls=[ProposedCall(
            name="check_prerequisites", arguments={"student_id": "S1000001", "code": "32933"},
            raw={"function": {"name": "check_prerequisites"}})]),
        ChatReply(content="NOT ELIGIBLE. 32933 requires 32144."),
    )
    transcript, _ = await cli.run_agent_one(
        elig(), spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=chat, manifest=manifest(),
    )
    run_dir = tmp_path / spec.run_id
    assert run_status(transcript) == "ok"
    declared = yaml.safe_load((run_dir / "task.yaml").read_text())
    assert "enrol" not in declared["allowed_tools"]
    assert declared["description"] == elig().question
    decisions = [json.loads(l) for l in (run_dir / "decisions.jsonl").read_text().splitlines() if l.strip()]
    assert decisions[0]["tool"] == "check_prerequisites" and decisions[0]["verdict"] == "allow"
    assert transcript.turns[-1].text.startswith("NOT ELIGIBLE")
