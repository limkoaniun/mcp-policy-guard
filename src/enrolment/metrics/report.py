"""Score a campaign directory, and write what a supervisor can check.

Read-only on the campaign: outputs go to a separate directory, so scoring can
be re-run, corrected and re-run again without any risk to the runs it scores.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..agent.campaign import MANIFEST, read_index
from ..agent.sampling import read_sample
from ..scenarios_spec import load_all
from .aggregate import aggregate
from .artifacts import baseline_path, read_run
from .judge import RunVerdict, judge_adversarial, judge_compliance

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
}


def _read_manifest(campaign_dir: Path) -> dict:
    return json.loads((campaign_dir / MANIFEST).read_text(encoding="utf-8"))


def _pack_path(manifest: dict, repo_root: Path) -> Path:
    pack = Path(manifest["policy_pack"])
    return pack if pack.is_absolute() else repo_root / pack


def score_campaign(campaign_dir, *, repo_root=REPO_ROOT) -> list[RunVerdict]:
    """Every run in `campaign_dir`, judged.

    The campaign's kind comes from the manifest's `sample_digest` -- set only
    by the compliance CLI, `None` for adversarial -- not from whether
    `sample.jsonl` happens to exist. A file's mere presence is not the
    authority: `read_sample` returns `[]` both when the file is absent and
    when it exists but is empty or truncated, and treating those the same as
    "no sample was drawn" would silently score a broken compliance campaign
    as an adversarial one.
    """
    campaign_dir = Path(campaign_dir)
    repo_root = Path(repo_root)
    records = read_index(campaign_dir)
    manifest = _read_manifest(campaign_dir)
    sample_digest = manifest.get("sample_digest")
    sample = {s.scenario_id: s for s in read_sample(campaign_dir)}

    if sample_digest is not None and not sample:
        raise ValueError(
            f"{campaign_dir} names sample_digest {sample_digest!r} in its "
            f"manifest, but sample.jsonl is absent or empty, so there is no "
            f"sample to score this compliance campaign against"
        )

    baseline = json.loads(
        baseline_path(_pack_path(manifest, repo_root), repo_root).read_text(
            encoding="utf-8"
        )
    )

    verdicts: list[RunVerdict] = []

    if sample_digest is not None:
        for record in records:
            verdicts.append(
                judge_compliance(
                    record, read_run(campaign_dir / record.run_id),
                    sample[record.scenario_id], baseline,
                )
            )
        return verdicts

    scenarios = {
        s.id: s
        for s in load_all(
            repo_root / "attacks" / "scenarios",
            personas=set(baseline), known_tools=KNOWN_TOOLS,
        )
    }
    students = set(baseline)
    for record in records:
        verdicts.append(
            judge_adversarial(
                record, read_run(campaign_dir / record.run_id),
                scenarios[record.scenario_id], baseline, students,
            )
        )
    return verdicts


def _print_summary(figures: dict) -> None:
    total, scored = figures["total"], figures["scored"]
    print(f"scored {scored} of {total} runs")
    for reason, count in figures["exclusions"].items():
        if count:
            print(f"  excluded {count:5} {reason}")

    if figures["attack_success"]:
        print("\nattack success rate (numerator/denominator):")
        for cls, arms in figures["attack_success"].items():
            cells = []
            for arm, rate in arms.items():
                if rate["denominator"] == 0:
                    cells.append(f"{arm}: not measurable")
                else:
                    cells.append(
                        f"{arm}: {rate['numerator']}/{rate['denominator']} "
                        f"({rate['rate']:.0%})"
                    )
            print(f"  {cls:24} " + "   ".join(cells))

    depth = figures["violation"]["by_depth"]
    if depth:
        print("\nviolation rate by rule depth (CORE scenarios only):")
        for level, arms in depth.items():
            cells = [
                f"{arm}: {r['numerator']}/{r['denominator']}"
                for arm, r in arms.items()
            ]
            print(f"  depth {level}                " + "   ".join(cells))

    modes = figures["violation"]["by_mode"]
    if modes:
        print("\nviolation rate by failure mode (WHOLE sample):")
        for mode, arms in modes.items():
            cells = [
                f"{arm}: {r['numerator']}/{r['denominator']}"
                for arm, r in arms.items()
            ]
            print(f"  {mode:24} " + "   ".join(cells))

    print("\nenforcement overhead (policy decision latency):")
    for arm, over in figures["overhead"].items():
        if over["calls"]:
            print(f"  {arm:4} p50 {over['p50_ms']:.3f} ms   "
                  f"p95 {over['p95_ms']:.3f} ms   over {over['calls']} calls")
        else:
            print(f"  {arm:4} no calls")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score a campaign into the specification's §7 metrics."
    )
    parser.add_argument("--campaign", required=True, help="campaign directory to score")
    parser.add_argument(
        "--out", required=True,
        help="where to write metrics.json and judgements.jsonl. Never inside "
             "the campaign: scoring is read-only on the runs it scores.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    verdicts = score_campaign(args.campaign)
    figures = aggregate(verdicts)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(figures, indent=2), encoding="utf-8")
    (out / "judgements.jsonl").write_text(
        "".join(json.dumps(v.model_dump(), ensure_ascii=False) + "\n" for v in verdicts),
        encoding="utf-8",
    )

    _print_summary(figures)
    print(f"\nwrote {out / 'metrics.json'} and {out / 'judgements.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
