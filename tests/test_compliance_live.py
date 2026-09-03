"""One real compliance run: real model, real proxy, real dataset.

An experiment, not a test -- skipped loudly when no daemon has the model
pulled, because a test that proves nothing when it does not run must not look
like a pass.
"""

import json
import pathlib
import urllib.error
import urllib.request

import pytest

from enrolment.agent.campaign import RunSpec, run_status
from enrolment.agent.compliance import (
    build_manifest,
    load_catalogue,
    load_sample,
    parse_args,
    run_one,
)
from enrolment.agent.ollama import OLLAMA_URL, model_fingerprint, ollama_chat, warm_up
from enrolment.agent.sampling import sample_digest
from enrolment.agent.transcript import CallOutcome, Transcript

REPO = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO / "configs" / "enrolment.yaml"
LIVE_MODEL = "llama3.2:latest"


def _model_is_ready(model: str = LIVE_MODEL) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return model in {entry.get("name") for entry in payload.get("models") or []}


LIVE = pytest.mark.skipif(
    not _model_is_ready(),
    reason=(
        f"no Ollama daemon at {OLLAMA_URL} with {LIVE_MODEL!r} pulled -- "
        f"`ollama serve` and `ollama pull {LIVE_MODEL}` to run it. Skipped, "
        f"not passed: this test proves nothing when it does not run."
    ),
)


@LIVE
@pytest.mark.anyio
async def test_a_real_model_runs_a_real_compliance_scenario(tmp_path):
    args = parse_args([
        "--campaign", str(tmp_path), "--models", LIVE_MODEL, "--repeats", "1",
    ])
    sampled = load_sample(args)
    scenario = next(s for s in sampled if not s.expected_satisfied)
    # The real catalogue name, not "": this is the one place the first
    # user turn the campaign actually sends gets composed end to end.
    subject_name = load_catalogue(args)[scenario.subject_code]
    assert subject_name
    warm_up(LIVE_MODEL)
    manifest = build_manifest(
        args, {LIVE_MODEL: model_fingerprint(LIVE_MODEL)}, sample_digest(sampled)
    )
    spec = RunSpec(
        scenario_id=scenario.scenario_id, model=LIVE_MODEL, arm="on", repeat=1
    )

    transcript, duration = await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=lambda m: ollama_chat(m, temperature=0.0),
        manifest=manifest, subject_name=subject_name,
    )

    run_dir = tmp_path / spec.run_id
    assert (run_dir / "transcript.jsonl").exists()
    reread = Transcript.read_jsonl(run_dir / "transcript.jsonl")
    assert reread.task_id == scenario.scenario_id
    assert run_status(reread) == run_status(transcript)
    assert json.loads((run_dir / "agent.json").read_text())["digest"] != "unknown"

    # The adversarial live test's assertions, deliberately in its exact form.
    # `>=` and `<=` rather than equality: the proxy may log a decision the
    # transcript has no call for -- an initialisation probe, a call the loop
    # dropped -- and B chose these shapes over a strict `==` for that reason.
    # They also stay meaningful when the model made no tool calls at all, so a
    # harness regression that suppressed every call fails here rather than
    # presenting as a skip. Zero calls is a model behaviour and is reported in
    # the print below, not skipped over.
    decisions = [
        json.loads(line)
        for line in (run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reached_proxy = [
        c for turn in transcript.turns for c in turn.calls
        if c.outcome in {CallOutcome.FORWARDED, CallOutcome.REFUSED}
    ]
    assert [d["task_id"] for d in decisions] == [scenario.scenario_id] * len(decisions)
    assert len(decisions) >= len(reached_proxy)
    assert {c.tool for c in reached_proxy} <= {d["tool"] for d in decisions}
    assert all(d["verdict"] for d in decisions)

    print(
        f"\none compliance run ({scenario.scenario_id}, {LIVE_MODEL}, "
        f"enforcement-on, 1 repeat, {len(reached_proxy)} calls reaching the "
        f"proxy, truncated={transcript.truncated}) took {duration:.1f}s, so "
        f"4960 runs is AT LEAST {4960 * duration / 3600:.1f} hours. A LOWER "
        f"BOUND from one cell: the larger model, the off arm and any run "
        f"reaching more tool calls all cost more than this cell did."
    )
