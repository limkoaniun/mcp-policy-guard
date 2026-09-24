"""Subsystem E: the RAG arm and the guarded agent on the same advice tasks.

Two arms per task. `rag` calls `enrolment.rag.answer` -- one completion, no
tools, no proxy. `agent` runs the guarded agent through the real proxy with a
read-only allowlist, exactly as the compliance runner does, minus the nudge:
a question needs no second turn. Both write a transcript the campaign
machinery already reads, so resume and replacement work unchanged.

E drives and records. Scoring is `enrolment.metrics.advice`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anyio
from pydantic import BaseModel

from policy_guard.upstream import connect_subprocess

from ..advice import AdviceTask, eligibility_tasks, session_tasks, write_tasks
from ..models import Course, Student, Subject, SubjectRequisites
from ..rag import answer as rag_answer
from ..run_setup import write_task
from ..scenarios import read_jsonl
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
from .prompts import PROMPTS, RAG_SYSTEM_PROMPT
from .sampling import sample_digest, stratified_sample
from .transcript import ResponseOutcome, Transcript, TurnRecord

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODELS = ["llama3.1:8b", "llama3.2"]
DEFAULT_PACK = "configs/enrolment.yaml"
DEFAULT_SUITE = "data/compliance_scenarios.jsonl"
KINDS = ("eligibility", "session")
ARMS = ("rag", "agent")

#: The guarded agent's allowlist on advice tasks: everything that reads or
#: plans, nothing that acts. A question is not a request to change anything.
ADVICE_TOOLS = [
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "get_study_plan", "get_session_options", "preview_major_change",
]


class World(BaseModel):
    """The data both arms read: the same files the tool server serves."""

    students: dict[str, Student]
    catalogue: dict[str, Subject]
    requisites: dict[str, SubjectRequisites]
    course: Course
    aos: dict[str, Course]


def _load(path, model):
    return {k: model.model_validate(v) for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}


def load_world(repo_root, students_path) -> World:
    data = Path(repo_root) / "data"
    return World(
        students=_load(students_path, Student),
        catalogue=_load(data / "catalogue.json", Subject),
        requisites=_load(data / "requisites.json", SubjectRequisites),
        course=Course.model_validate(json.loads((data / "course.json").read_text(encoding="utf-8"))),
        aos=_load(data / "aos.json", Course),
    )


def enumerate_matrix(tasks, models, repeats: int, arms) -> list[RunSpec]:
    specs = []
    for repeat in range(1, repeats + 1):
        for model in models:
            for task in tasks:
                for arm in arms:
                    specs.append(RunSpec(scenario_id=task.task_id, model=model, arm=arm, repeat=repeat))
    return specs


def task_for(task: AdviceTask, system_prompt: str) -> AgentTask:
    return AgentTask(
        task_id=task.task_id,
        system=system_prompt.replace("{principal}", task.student_id),
        turns=[task.question],
    )


async def run_rag_one(task, spec, *, campaign_dir, chat_factory, manifest, world) -> tuple[Transcript, float]:
    run_dir = Path(campaign_dir) / spec.run_id
    clear_run_dir(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    try:
        reply = rag_answer(
            task.question, student=world.students[task.student_id], catalogue=world.catalogue,
            requisites=world.requisites, course=world.course, aos=world.aos,
            chat=chat_factory(spec.model), model=spec.model,
            system_prompt=manifest.rag_system_prompt, session=task.session,
        )
    except Exception as exc:  # noqa: BLE001 - mirrors loop.py: any provider failure ends this run, recorded, replaceable
        duration = time.perf_counter() - started
        failure = f"{type(exc).__name__}: {exc}"
        transcript = Transcript(task_id=task.task_id, model=spec.model, truncated=True,
                                truncation_reason=f"provider error: {failure}",
                                turns=[TurnRecord(user_turn=0, model_turn=0,
                                                  outcome=ResponseOutcome.PROVIDER_ERROR, text=failure)])
        transcript.write_jsonl(run_dir / "transcript.jsonl")
        return transcript, duration
    duration = time.perf_counter() - started
    (run_dir / "rag.json").write_text(json.dumps(
        {"question": task.question, "context": reply.context, "text": reply.text,
         "latency_ms": reply.latency_ms}, ensure_ascii=False, indent=2), encoding="utf-8")
    transcript = Transcript(task_id=task.task_id, model=spec.model, turns=[TurnRecord(
        user_turn=0, model_turn=0, outcome=ResponseOutcome.TEXT, text=reply.text,
        latency_ms=reply.latency_ms)])
    transcript.write_jsonl(run_dir / "transcript.jsonl")
    (run_dir / "agent.json").write_text(json.dumps(
        manifest.fingerprints.get(spec.model, {"model": spec.model}), indent=2), encoding="utf-8")
    return transcript, duration


async def run_agent_one(task, spec, *, campaign_dir, pack_path, repo_root, chat_factory, manifest) -> tuple[Transcript, float]:
    run_dir = Path(campaign_dir) / spec.run_id
    clear_run_dir(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    task_path = write_task(
        run_dir, task_id=task.task_id, description=task.question,
        allowed_tools=ADVICE_TOOLS, principal_id=task.student_id,
    )
    arguments = [
        "-m", "policy_guard", "--task", str(task_path), "--policy-pack", str(pack_path),
        "--run-dir", str(run_dir), "--enforcement", "on",
    ]
    started = time.perf_counter()
    async with connect_subprocess(sys.executable, arguments, cwd=str(repo_root)) as client:
        transcript = await run_agent(
            task_for(task, manifest.system_prompt), client, chat_factory(spec.model),
            model=spec.model, max_model_turns=manifest.max_model_turns, max_calls=manifest.max_calls,
        )
    duration = time.perf_counter() - started
    transcript.write_jsonl(run_dir / "transcript.jsonl")
    (run_dir / "agent.json").write_text(json.dumps(
        manifest.fingerprints.get(spec.model, {"model": spec.model}), indent=2), encoding="utf-8")
    return transcript, duration


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the advice tasks on the RAG arm and the guarded agent (subsystem E).")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        type=lambda v: [m.strip() for m in v.split(",") if m.strip()])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--kinds", default=",".join(KINDS),
                        type=lambda v: [k.strip() for k in v.split(",") if k.strip()])
    parser.add_argument("--arms", default=",".join(ARMS),
                        type=lambda v: [a.strip() for a in v.split(",") if a.strip()])
    parser.add_argument("--per-depth", type=int, default=40)
    parser.add_argument("--mode-floor", type=int, default=25)
    parser.add_argument("--sample-seed", type=int, default=20260903)
    parser.add_argument("--pack", default=DEFAULT_PACK)
    parser.add_argument("--suite", default=DEFAULT_SUITE)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--max-model-turns", type=int, default=4)
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument("--num-ctx", type=int, default=8192,
                        help="Ollama's context window; long RAG prompts silently truncate without it")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--prompt", default="default", choices=sorted(PROMPTS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    unknown = [k for k in args.kinds if k not in KINDS] + [a for a in args.arms if a not in ARMS]
    if unknown:
        parser.error(f"unknown kinds/arms {unknown}; kinds are {KINDS}, arms are {ARMS}")
    return args


def build_tasks(args, world: World) -> tuple[list[AdviceTask], str]:
    tasks: list[AdviceTask] = []
    digest = ""
    if "eligibility" in args.kinds:
        sampled = stratified_sample(read_jsonl(REPO_ROOT / args.suite), per_depth=args.per_depth,
                                    mode_floor=args.mode_floor, seed=args.sample_seed)
        digest = sample_digest(sampled)
        names = {code: s.name for code, s in world.catalogue.items()}
        tasks += eligibility_tasks(sampled, names)
    if "session" in args.kinds:
        tasks += session_tasks(world.students, world.course, world.aos, world.catalogue)
    return tasks, digest


def build_manifest(args, fingerprints: dict, digest: str) -> CampaignManifest:
    return CampaignManifest(
        models=list(args.models), repeats=args.repeats, system_prompt=PROMPTS[args.prompt],
        rag_system_prompt=RAG_SYSTEM_PROMPT,
        # Advice tasks are one turn -- a question, not a request to act -- so
        # there is no second turn for the compliance/adversarial nudge to
        # occupy. "" (rather than NUDGE) records that plainly, and a
        # campaign resumed under the shared NUDGE would rightly be refused
        # as a different method.
        nudge="", policy_pack=args.pack, max_model_turns=args.max_model_turns,
        max_calls=args.max_calls, temperature=args.temperature, seed=args.seed,
        fingerprints=fingerprints, sample_digest=digest or None, num_ctx=args.num_ctx,
    )


def _students_path(args) -> Path:
    from policy_guard.config import load_policy_pack
    pack = load_policy_pack(REPO_ROOT / args.pack)
    return REPO_ROOT / pack.domain_rules_args["students_path"]


def main(argv=None) -> int:
    args = parse_args(argv)
    world = load_world(REPO_ROOT, _students_path(args))
    tasks, digest = build_tasks(args, world)
    specs = enumerate_matrix(tasks, args.models, args.repeats, args.arms)
    if args.limit is not None:
        specs = specs[: args.limit]
    by_kind = {k: sum(1 for t in tasks if t.kind == k) for k in KINDS}
    print(f"{len(tasks)} tasks ({by_kind['eligibility']} eligibility, {by_kind['session']} session)")
    print(f"{len(specs)} runs: " + ", ".join(f"{sum(1 for s in specs if s.arm == a)} {a}" for a in args.arms))
    if args.limit is not None:
        print(f"--limit {args.limit}: running the first {len(specs)} of them")
    if args.dry_run:
        return 0

    fingerprints = preflight(args.models)
    manifest = load_or_create_manifest(args.campaign, build_manifest(args, fingerprints, digest))
    warn_on_digest_drift(manifest.fingerprints, fingerprints)
    campaign_dir = Path(args.campaign)
    write_tasks(campaign_dir, tasks)
    by_id = {t.task_id: t for t in tasks}
    pack_path = REPO_ROOT / args.pack

    def chat_factory(model):
        return ollama_chat(model, temperature=manifest.temperature, seed=manifest.seed,
                            num_ctx=manifest.num_ctx)

    async def runner(spec):
        task = by_id[spec.scenario_id]
        if spec.arm == "rag":
            transcript, duration = await run_rag_one(
                task, spec, campaign_dir=campaign_dir, chat_factory=chat_factory,
                manifest=manifest, world=world)
        else:
            transcript, duration = await run_agent_one(
                task, spec, campaign_dir=campaign_dir, pack_path=pack_path, repo_root=REPO_ROOT,
                chat_factory=chat_factory, manifest=manifest)
        return run_status(transcript), duration

    def describe(spec):
        return {"scenario_id": spec.scenario_id, "attack_class": None, "model": spec.model,
                "arm": spec.arm, "repeat": spec.repeat, "attempt": spec.attempt}

    records = anyio.run(lambda: run_campaign(specs, runner, campaign_dir=campaign_dir, describe=describe))
    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    timed = [r.duration_s for r in records if r.duration_s is not None]
    print(f"{len(records)} runs indexed: {counts}")
    print(f"{sum(timed) / 60:.1f} minutes of run time across {len(timed)} timed runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
