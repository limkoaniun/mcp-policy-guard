"""One real run: real model, real proxy, real dataset.

An experiment, not a test -- so it is skipped loudly when no daemon has the
model pulled, and the skip says so. A test that proves nothing when it does
not run must not look like a pass.
"""

import json
import pathlib
import urllib.error
import urllib.request

import pytest

from enrolment.agent.adversarial import RunSpec, build_manifest, parse_args, run_one
from enrolment.agent.campaign import existing_status, run_status
from enrolment.agent.ollama import OLLAMA_URL, model_fingerprint, ollama_chat, warm_up
from enrolment.agent.transcript import CallOutcome, Transcript
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
async def test_a_real_model_runs_a_real_scenario_end_to_end(tmp_path):
    scenario = {
        s.id: s for s in load_all(SCENARIOS, personas=PERSONAS, known_tools=KNOWN_TOOLS)
    }["SC-07"]
    warm_up(LIVE_MODEL)

    args = parse_args(["--campaign", str(tmp_path), "--models", LIVE_MODEL, "--repeats", "1"])
    manifest = build_manifest(args, {LIVE_MODEL: model_fingerprint(LIVE_MODEL)})
    spec = RunSpec(scenario_id="SC-07", model=LIVE_MODEL, arm="on", repeat=1)

    transcript, duration = await run_one(
        scenario, spec, campaign_dir=tmp_path, pack_path=PACK, repo_root=REPO,
        chat_factory=lambda model: ollama_chat(model, temperature=0.0),
        manifest=manifest,
    )

    run_dir = tmp_path / spec.run_id
    assert (run_dir / "transcript.jsonl").exists()
    assert (run_dir / "overlay.yaml").exists()
    assert json.loads((run_dir / "agent.json").read_text())["digest"] != "unknown"

    # The transcript reads back with the header its writer promised. This is
    # the artifact resume and scoring both read, and the only place a harness
    # bound becomes visible -- the proxy's `complete` marker is written on
    # clean shutdown whether or not the loop was truncated.
    lines = (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    assert set(header) == {"task_id", "model", "truncated", "truncation_reason"}
    assert header["task_id"] == "SC-07"
    assert header["model"] == LIVE_MODEL
    assert len(lines) == 1 + len(transcript.turns)
    assert Transcript.read_jsonl(run_dir / "transcript.jsonl").truncated == transcript.truncated
    assert existing_status(run_dir) == run_status(transcript)

    # decisions.jsonl carries this run's own lines: every call the transcript
    # records as having reached the proxy is accounted for there, and nothing
    # in there belongs to another task. Joining these two files is what the
    # scoring subsystem does, so they have to agree.
    decisions = [
        json.loads(line)
        for line in (run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reached_proxy = [
        c for turn in transcript.turns for c in turn.calls
        if c.outcome in {CallOutcome.FORWARDED, CallOutcome.REFUSED}
    ]
    assert [d["task_id"] for d in decisions] == ["SC-07"] * len(decisions)
    assert len(decisions) >= len(reached_proxy)
    assert {c.tool for c in reached_proxy} <= {d["tool"] for d in decisions}
    assert all(d["verdict"] for d in decisions)

    # Spec §12: the campaign estimate comes from a measurement, not a guess --
    # and it is labelled as what it is. This is ONE cell: SC-07, the smaller
    # of the two models, enforcement-on, one repeat. `llama3.1:8b` is larger;
    # the off arm forwards calls the on arm refuses; and a run that reaches
    # max_model_turns=4 costs several times a depth-1 run. So 280x this is a
    # lower bound on the campaign, never an estimate of it, and the figure has
    # to reach the report carrying that caveat.
    print(
        f"\nlower bound only -- one cell (SC-07, {LIVE_MODEL}, enforcement-on, "
        f"1 repeat, {len(reached_proxy)} calls, truncated={transcript.truncated}) "
        f"took {duration:.1f}s, so 280 runs is AT LEAST "
        f"{280 * duration / 3600:.1f} hours. The larger model, the off arm and "
        f"any run reaching max_model_turns all cost more than this cell did."
    )
