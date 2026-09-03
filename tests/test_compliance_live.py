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
from enrolment.agent.compliance import build_manifest, load_sample, parse_args, run_one
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
        manifest=manifest, subject_name="",
    )

    run_dir = tmp_path / spec.run_id
    assert (run_dir / "transcript.jsonl").exists()
    reread = Transcript.read_jsonl(run_dir / "transcript.jsonl")
    assert reread.task_id == scenario.scenario_id
    assert run_status(reread) == run_status(transcript)
    assert json.loads((run_dir / "agent.json").read_text())["digest"] != "unknown"

    reached = [
        c for turn in transcript.turns for c in turn.calls
        if c.outcome in {CallOutcome.FORWARDED, CallOutcome.REFUSED}
    ]
    decisions_path = run_dir / "decisions.jsonl"
    if reached:
        lines = [
            line for line in decisions_path.read_text().splitlines() if line.strip()
        ]
        assert len(lines) == len(reached)
    else:
        pytest.skip(
            "the model made no tool calls in this run -- the plumbing could not "
            "be exercised. A model behaviour, not a harness failure, but "
            "reported rather than passed."
        )

    print(
        f"\none compliance run took {duration:.1f}s; 4960 runs projects to "
        f"{4960 * duration / 3600:.1f} hours. A LOWER BOUND from one cell of "
        f"{LIVE_MODEL} on the enforcement-on arm: the larger model and runs "
        f"that reach more tool calls cost more."
    )
