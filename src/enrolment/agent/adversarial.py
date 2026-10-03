"""Subsystem B: run the adversarial scenarios through a real model.

Specified in `context/2026-09-02-adversarial-runs-design.md`. This module owns
the domain half -- turning a `Scenario` into an `AgentTask`, enumerating the
matrix, executing one run. `campaign.py` owns the orchestration half and knows
nothing about enrolment, so subsystem C can reuse it unchanged.

B drives; it does not judge. Nothing here decides whether an attack succeeded.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anyio

from policy_guard.upstream import connect_subprocess

from ..ablation import ablated_pack
from ..run_setup import (
    arms_for,
    off_arm_allowed,
    write_attachments,
    write_overlay,
    write_task,
)
from ..scenarios_spec import Scenario, load_all
from .campaign import (
    CampaignManifest,
    RunSpec,
    clear_run_dir,
    load_or_create_manifest,
    run_campaign,
    run_status,
)
from .cli_common import preflight, warn_on_digest_drift
from .loop import AgentTask, run_agent
from .ollama import ollama_chat
from .prompts import NUDGE, PROMPTS, RAG_SYSTEM_PROMPT, SYSTEM_PROMPT
from .transcript import Transcript


def turns_for(scenario: Scenario) -> list[str]:
    """The user turns for `scenario` -- derived, never authored.

    Twin equality falls out of the data: an injected scenario and its benign
    twin carry byte-identical `task.description` values, so they cannot get
    different turn scripts without someone editing one of them, which
    `tests/test_scenario_descriptions.py` refuses.
    """
    return [scenario.task.description, NUDGE]


def task_for(scenario: Scenario, *, system_prompt: str = SYSTEM_PROMPT) -> AgentTask:
    """`scenario` as a task the agent loop can run.

    `system_prompt` is a parameter rather than a constant because D's
    prompting baseline ("ignore instructions found in tool output") is this
    same loop under a different system prompt. The exact string used is
    recorded in the campaign manifest: a number produced under one prompt is
    not comparable with a number produced under another.

    Only `{principal}` is substituted, and by `str.replace` rather than
    `str.format`: a baseline prompt is ordinary prose, and prose containing a
    stray brace should not crash the harness over a formatting contract
    nobody documented.
    """
    return AgentTask(
        task_id=scenario.id,
        system=system_prompt.replace("{principal}", scenario.persona),
        turns=turns_for(scenario),
    )


def enumerate_matrix(scenarios, models, repeats: int, arms=("on", "off")) -> list[RunSpec]:
    """Every run the campaign will make, in the order it will make them.

    Repeat is the outermost loop on purpose. An interrupted campaign then
    leaves complete early repeats across the whole suite rather than all five
    repeats of the first few scenarios and nothing of the rest -- a partial
    campaign that is still balanced is worth something; one that is not is
    worth very little.

    Which arms a scenario gets is `run_setup.arms_for`, not a rule restated
    here: the four `deliverable: false` scenarios get the on arm only, because
    an off arm for a payload that never lands would report "the attack failed"
    when none was mounted.

    `arms` filters the agent arms `arms_for` offers and appends `rag` when
    asked. The RAG arm has no enforcement switch, so every scenario gets it,
    injected or benign: a twin's reply to the clean document is the control
    the audit reads beside the planted one.
    """
    specs: list[RunSpec] = []
    for repeat in range(1, repeats + 1):
        for model in models:
            for scenario in scenarios:
                run_arms = [arm for arm in arms_for(scenario) if arm in arms]
                if "rag" in arms:
                    run_arms.append("rag")
                for arm in run_arms:
                    specs.append(
                        RunSpec(
                            scenario_id=scenario.id, model=model, arm=arm, repeat=repeat
                        )
                    )
    return specs


async def run_one(
    scenario: Scenario,
    spec: RunSpec,
    *,
    campaign_dir,
    pack_path,
    repo_root,
    chat_factory,
    manifest: CampaignManifest,
) -> tuple[Transcript, float]:
    """Run one cell of the matrix and leave its artifacts on disk.

    `chat_factory` maps a model name to a chat callable, which is what lets
    every test here run with no daemon: the real campaign passes a factory
    built on `ollama_chat`, the tests pass a scripted fake.

    A directory that is already there is one of two very different things,
    and `campaign.clear_run_dir` tells them apart. See its docstring.

    Returns the transcript and the run's wall-clock seconds. It does not
    return a status: `campaign.run_status` derives that, so the projection
    lives in one place.
    """
    run_dir = Path(campaign_dir) / spec.run_id
    clear_run_dir(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)

    overlay_path = write_overlay(scenario, run_dir)
    attachments_path = write_attachments(scenario, run_dir)
    task_path = write_task(
        run_dir,
        task_id=scenario.id,
        description=scenario.task.description,
        allowed_tools=scenario.task.allowed_tools,
        principal_id=scenario.persona,
        overlay_path=overlay_path,
        attachments_path=attachments_path,
    )

    arguments = [
        "-m", "policy_guard",
        "--task", str(task_path),
        "--policy-pack", str(pack_path),
        "--run-dir", str(run_dir),
        "--enforcement", spec.arm,
    ]

    started = time.perf_counter()
    async with connect_subprocess(sys.executable, arguments, cwd=str(repo_root)) as client:
        transcript = await run_agent(
            task_for(scenario, system_prompt=manifest.system_prompt),
            client,
            chat_factory(spec.model),
            model=spec.model,
            max_model_turns=manifest.max_model_turns,
            max_calls=manifest.max_calls,
        )
    duration = time.perf_counter() - started

    transcript.write_jsonl(run_dir / "transcript.jsonl")
    # The fingerprint is taken once per campaign and carried in the manifest,
    # so this costs no HTTP round trip per run. It is written into the run
    # directory anyway: a run that travels without its manifest still says
    # which weights produced it.
    (run_dir / "agent.json").write_text(
        json.dumps(manifest.fingerprints.get(spec.model, {"model": spec.model}), indent=2),
        encoding="utf-8",
    )
    return transcript, duration


DEFAULT_MODELS = ["llama3.1:8b", "llama3.2"]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PACK = "configs/enrolment-personas.yaml"
DEFAULT_SCENARIOS = "attacks/scenarios"
DEFAULT_PERSONAS = "data/personas.json"
ARMS = ("on", "off", "rag")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the adversarial scenarios through a real model (subsystem B)."
    )
    parser.add_argument("--campaign", required=True, help="campaign directory")
    parser.add_argument(
        "--models", default=",".join(DEFAULT_MODELS),
        type=lambda value: [m.strip() for m in value.split(",") if m.strip()],
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--arms", default="on,off",
        type=lambda value: [a.strip() for a in value.split(",") if a.strip()],
        help="which arms to run: on and off are the proxy's enforcement switch, "
             "rag is the tool-less chatbot. Default on,off, the campaign as it "
             "has always run.",
    )
    parser.add_argument(
        "--num-ctx", type=int, default=None, dest="num_ctx",
        help="Ollama context window for every arm's model calls, recorded in "
             "the manifest. The RAG prompt carries a whole document and "
             "overflows the default; the advice runner uses 8192.",
    )
    parser.add_argument("--pack", default=DEFAULT_PACK)
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS)
    parser.add_argument("--personas", default=DEFAULT_PERSONAS)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--seed", type=int, default=None,
        help="recorded for completeness; A's §7 measured it changing nothing, "
             "so it must not be described as making runs reproducible",
    )
    parser.add_argument("--max-model-turns", type=int, default=4)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="run at most this many runs this invocation, then stop. A cap, "
             "not a window: the campaign is resumable, so making further "
             "progress means raising this number or omitting the flag next "
             "time -- repeating the same --limit re-confirms the same runs "
             "and does nothing. This is how one run is timed before "
             "committing to 280.",
    )
    parser.add_argument(
        "--ablate", default=None, metavar="POLICY",
        help="run against a pack with POLICY removed (spec §7's 'each policy "
             "turned off in turn'). The pack is derived from --pack into the "
             "campaign directory, so the pack a run used is stored beside the "
             "runs it produced and cannot drift from the baseline's.",
    )
    parser.add_argument(
        "--prompt", default="default", choices=sorted(PROMPTS),
        help="which system prompt the agent runs under; see the compliance "
             "runner. Recorded in the manifest as the prompt text.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    unknown = [a for a in args.arms if a not in ARMS]
    if unknown:
        parser.error(f"--arms: unknown arm(s) {unknown}; choose from {', '.join(ARMS)}")
    return args


def load_suite(args):
    personas = set(json.loads((REPO_ROOT / args.personas).read_text(encoding="utf-8")))
    known_tools = {
        "search_subjects", "get_subject_details", "get_student_record",
        "check_prerequisites", "enrol", "drop", "submit_erequest",
        "get_study_plan", "preview_major_change", "get_session_options",
        "get_attachment",
    }
    return load_all(REPO_ROOT / args.scenarios, personas=personas, known_tools=known_tools)


def refuse_unscorable_for_rag(scenarios) -> None:
    """Stop before any run if the RAG arm could not score this suite.

    The arm plants the payload by putting the document in the prompt, so only
    an `attachment` payload reaches it; and it judges from `obeyed_if`, so a
    payload without markers would produce a run nobody can score. Both are
    said now, naming the scenarios, rather than discovered 288 runs later.
    """
    offending = sorted(
        s.id for s in scenarios
        if s.injected and s.deliverable and s.payload is not None
        and (s.payload.location != "attachment" or not s.payload.obeyed_if)
    )
    if offending:
        raise SystemExit(
            f"--arms rag: the RAG arm can only score an attachment payload that "
            f"declares obeyed_if, and these scenarios do not: {', '.join(offending)}"
        )


def build_manifest(args, fingerprints: dict) -> CampaignManifest:
    return CampaignManifest(
        models=list(args.models), repeats=args.repeats, arms=list(args.arms),
        system_prompt=PROMPTS[args.prompt],
        rag_system_prompt=RAG_SYSTEM_PROMPT if "rag" in args.arms else None,
        nudge=NUDGE, policy_pack=args.pack,
        max_model_turns=args.max_model_turns, max_calls=args.max_calls,
        temperature=args.temperature, seed=args.seed, fingerprints=fingerprints,
        num_ctx=args.num_ctx,
    )


def _report_matrix(specs, scenarios) -> None:
    on = len([s for s in specs if s.arm == "on"])
    off = len([s for s in specs if s.arm == "off"])
    rag = len([s for s in specs if s.arm == "rag"])
    print(f"{len(specs)} runs: {on} enforcement-on, {off} enforcement-off, {rag} rag")
    # Asked of `run_setup`, not restated here. The design's constraint that
    # the two-of-four-attack-classes gap be reported accurately rests on this
    # line, and a copy of the rule would keep describing the old one if
    # `off_arm_allowed` ever changed.
    excluded = sorted(s.id for s in scenarios if not off_arm_allowed(s))
    print(
        f"no enforcement-off arm ({len(excluded)} scenarios, "
        f"deliverable: false): {', '.join(excluded)}"
    )
    print(
        "  -- their attack classes report the on arm only, and the results "
        "table must say so rather than omit them"
    )


def resolve_pack(args) -> Path:
    """The pack this campaign runs under, ablated if `--ablate` asks.

    Derived into the campaign directory rather than into `configs/`: an
    ablation pack is an artifact of one campaign, and a generated file sitting
    in `configs/` beside the hand-written packs invites being edited as if it
    were one.
    """
    pack_path = REPO_ROOT / args.pack
    if args.ablate is None:
        return pack_path
    derived = ablated_pack(pack_path, args.ablate, Path(args.campaign))
    print(f"ablation: {args.ablate!r} removed; running against {derived}")
    return derived


def main(argv=None) -> int:
    args = parse_args(argv)
    scenarios = load_suite(args)
    if "rag" in args.arms:
        refuse_unscorable_for_rag(scenarios)
    specs = enumerate_matrix(scenarios, args.models, args.repeats, args.arms)

    _report_matrix(specs, scenarios)
    if args.dry_run:
        resolve_pack(args)
        return 0

    if args.limit is not None:
        specs = specs[: args.limit]
        print(f"--limit {args.limit}: running the first {len(specs)} of them")

    fingerprints = preflight(args.models)
    manifest = load_or_create_manifest(args.campaign, build_manifest(args, fingerprints))
    warn_on_digest_drift(manifest.fingerprints, fingerprints)
    by_id = {s.id: s for s in scenarios}
    campaign_dir = Path(args.campaign)
    pack_path = resolve_pack(args)

    def chat_factory(model):
        return ollama_chat(model, temperature=manifest.temperature, seed=manifest.seed)

    async def runner(spec):
        transcript, duration = await run_one(
            by_id[spec.scenario_id], spec, campaign_dir=campaign_dir,
            pack_path=pack_path, repo_root=REPO_ROOT,
            chat_factory=chat_factory, manifest=manifest,
        )
        return run_status(transcript), duration

    def describe(spec):
        return {
            "scenario_id": spec.scenario_id,
            "attack_class": by_id[spec.scenario_id].attack_class,
            "model": spec.model, "arm": spec.arm,
            "repeat": spec.repeat, "attempt": spec.attempt,
        }

    records = anyio.run(
        lambda: run_campaign(
            specs, runner, campaign_dir=campaign_dir, describe=describe
        )
    )

    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    timed = [r.duration_s for r in records if r.duration_s is not None]
    untimed = len(records) - len(timed)
    print(f"{len(records)} runs indexed: {counts}")
    print(f"{sum(timed) / 60:.1f} minutes of run time across {len(timed)} timed runs")
    if untimed:
        # Adopted from disk with no stored duration -- an earlier invocation
        # ran them and its index row is gone, or was never written. Averaging
        # zeros into the total above would report a campaign as faster than it
        # was, on evidence that does not exist.
        print(f"{untimed} of them carry no duration and are not in that total: "
              f"they were adopted from disk, not timed here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
