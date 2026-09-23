"""Score an advice campaign: RAG and the guarded agent on the same questions.

Separate from `report.py` because the rules differ. An advice run has no
state file and, on the RAG arm, no decisions; a reply that ignores the
format is a verdict ("no answer"), not an exclusion. Exclusions are the three
that say nothing about the model's answer: provider_error, truncated,
no_transcript.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..advice import AdviceTask, read_tasks, score
from ..agent.campaign import IndexRecord, read_index
from ..agent.transcript import ResponseOutcome, Transcript

EXCLUSIONS = ("provider_error", "truncated", "no_transcript")
TEXT_OUTCOMES = {ResponseOutcome.TEXT, ResponseOutcome.TEXT_RESEMBLING_A_CALL}


def final_text(transcript: Transcript) -> str:
    """The model's closing words: the last text turn, or "" if it never closed."""
    if not transcript.turns:
        return ""
    last = transcript.turns[-1]
    if last.outcome in TEXT_OUTCOMES and last.text.strip():
        return last.text
    return ""


def judge_run(record: IndexRecord, run_dir, task: AdviceTask) -> dict:
    base = {
        "run_id": record.run_id, "task_id": task.task_id, "kind": task.kind,
        "model": record.model, "arm": record.arm, "repeat": record.repeat,
        "rule_depth": task.rule_depth, "stratum": task.stratum,
        "duration_s": record.duration_s,
        "expected": task.expected_eligible if task.kind == "eligibility" else None,
    }
    path = Path(run_dir) / "transcript.jsonl"
    if record.status == "provider_error":
        return {**base, "scoreable": False, "excluded_because": "provider_error"}
    if not path.exists():
        return {**base, "scoreable": False, "excluded_because": "no_transcript"}
    transcript = Transcript.read_jsonl(path)
    if transcript.turns and transcript.turns[-1].outcome is ResponseOutcome.PROVIDER_ERROR:
        return {**base, "scoreable": False, "excluded_because": "provider_error"}
    if record.status == "truncated" or transcript.truncated:
        return {**base, "scoreable": False, "excluded_because": "truncated"}
    verdict = score(task, final_text(transcript))
    return {**base, "scoreable": True, "answered": verdict.answered, "correct": verdict.correct,
            "jaccard": verdict.jaccard, "extracted": verdict.extracted,
            "latency_ms": sum(t.latency_ms for t in transcript.turns)}


def _ratio(num, den):
    return {"numerator": num, "denominator": den}


def _mean(values) -> float | None:
    """The mean of the non-`None` values, or `None` when there are none.

    Wall-clock duration is `None` for a run adopted from disk that no
    invocation timed (campaign.py's own reasoning); averaging with those
    coerced to 0 would understate every cell that ever resumed."""
    present = [v for v in values if v is not None]
    return (sum(present) / len(present)) if present else None


def aggregate(judgements: list[dict]) -> dict:
    scoreable = [j for j in judgements if j.get("scoreable")]
    out: dict = {"total": len(judgements), "scored": len(scoreable),
                 "exclusions": dict(Counter(j["excluded_because"] for j in judgements if not j.get("scoreable")))}
    groups: dict = defaultdict(list)
    for j in scoreable:
        groups[(j["kind"], j["arm"], j["model"])].append(j)
    for (kind, arm, model), rows in sorted(groups.items()):
        answered = [j for j in rows if j["answered"]]
        cell = {
            "answered": _ratio(len(answered), len(rows)),
            "accuracy_over_answered": _ratio(sum(1 for j in answered if j["correct"]), len(answered)),
            "accuracy_over_all": _ratio(sum(1 for j in answered if j["correct"]), len(rows)),
            "mean_latency_ms": (sum(j.get("latency_ms", 0.0) for j in rows) / len(rows)) if rows else None,
            "mean_duration_s": _mean(j.get("duration_s") for j in rows),
        }
        if kind == "session":
            js = [j["jaccard"] for j in answered if j.get("jaccard") is not None]
            cell["mean_jaccard"] = (sum(js) / len(js)) if js else None
        if kind == "eligibility":
            by_depth: dict = {}
            core = [j for j in answered if j.get("stratum") == "core" and j.get("rule_depth") is not None]
            for depth in sorted({j["rule_depth"] for j in core}):
                at = [j for j in core if j["rule_depth"] == depth]
                by_depth[str(depth)] = _ratio(sum(1 for j in at if j["correct"]), len(at))
            cell["by_depth"] = by_depth
            #: Always guessing the more common of ELIGIBLE/NOT ELIGIBLE in
            #: this cell, without reading anything -- the floor any answer
            #: strategy must clear to be worth reporting as skill.
            true_count = sum(1 for j in rows if j.get("expected") is True)
            false_count = sum(1 for j in rows if j.get("expected") is False)
            cell["majority_baseline"] = _ratio(max(true_count, false_count), len(rows))
        out.setdefault(kind, {}).setdefault(arm, {})[model] = cell
    return out


def _print(metrics: dict) -> None:
    print(f"scored {metrics['scored']} of {metrics['total']} runs; exclusions {metrics['exclusions']}")
    for kind in ("eligibility", "session"):
        if kind not in metrics:
            continue
        print(f"\n{kind}:")
        for arm, models in metrics[kind].items():
            for model, cell in models.items():
                a, acc, al = cell["answered"], cell["accuracy_over_answered"], cell["accuracy_over_all"]
                extra = f"  mean jaccard {cell['mean_jaccard']:.2f}" if cell.get("mean_jaccard") is not None else ""
                duration = f"  mean duration {cell['mean_duration_s']:.2f}s" if cell.get("mean_duration_s") is not None else ""
                print(f"  {arm:6} {model:14} answered {a['numerator']}/{a['denominator']}  "
                      f"correct/answered {acc['numerator']}/{acc['denominator']}  "
                      f"correct/all {al['numerator']}/{al['denominator']}{extra}{duration}")
                if cell.get("by_depth"):
                    print("         by depth (core): " + ", ".join(
                        f"{d}: {r['numerator']}/{r['denominator']}" for d, r in cell["by_depth"].items()))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Score an advice campaign.")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--out", required=True, help="never inside the campaign directory")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    campaign = Path(args.campaign).resolve()
    out = Path(args.out).resolve()
    if campaign in out.parents or campaign == out:
        raise SystemExit("--out must not be inside the campaign directory: scoring is read-only on runs")
    tasks = {t.task_id: t for t in read_tasks(campaign)}
    judgements = [judge_run(r, campaign / r.run_id, tasks[r.scenario_id]) for r in read_index(campaign)]
    metrics = aggregate(judgements)
    out.mkdir(parents=True, exist_ok=True)
    (out / "judgements.jsonl").write_text("".join(json.dumps(j) + "\n" for j in judgements), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    _print(metrics)
    print(f"\nwrote {out / 'metrics.json'} and {out / 'judgements.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
