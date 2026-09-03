"""Draw the compliance sample, and record exactly which one was drawn.

Running the whole suite is 4,650 scenarios x 2 models x 5 repeats x 2 arms =
93,000 runs, about 77 hours at the throughput subsystem B measured. So C
samples, and which sample it draws decides what the report can claim.

Two rules, both from the design:

**Equal allocation per depth (§4).** Proportional sampling would put the
thinnest sample at depth 4 -- the deep end, where a failure is most likely and
therefore where the estimate most needs power. The literature review is why
this matters more than it looks: *Logical Phase Transitions* reports reasoning
collapsing abruptly past a threshold rather than degrading gradually, while
the specification expects a gradient. If it is a cliff, the sample's job is to
locate it, and locating a cliff needs equal power either side of wherever it
falls.

**A floor per failure mode (§5).** Depth allocation alone draws about 1.3
`unmet_aos` and 3.1 `unmet_credit_points` out of 200, and a violation rate
computed on one scenario is not a rate. Both are checks this enforcement layer
is distinctive for.

The floor unbalances the depths, deliberately and unavoidably, because the
rare modes live at particular depths. That is why every scenario is labelled
`core` or `top_up`: the depth curve is computed from the core, per-mode rates
from the whole, and a figure mixing the two denominators is wrong.

This module draws and records. It computes no rate and makes no judgement.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..scenarios import ComplianceScenario

SAMPLE = "sample.jsonl"


class SampledScenario(BaseModel):
    """One drawn scenario, with the stratum that decides its denominator.

    `stratum` is not decoration. `core` scenarios are depth-balanced and are
    the only ones the depth curve may be computed from; `top_up` scenarios
    exist to make a rare mode measurable and would bias that curve. Mixing
    them is the error this field exists to prevent.
    """

    scenario_id: str
    student_id: str
    subject_code: str
    failure_mode: str
    rule_depth: int
    expected_satisfied: bool
    explanation: str
    stratum: Literal["core", "top_up"]

    @classmethod
    def of(cls, scenario: ComplianceScenario, stratum: str) -> SampledScenario:
        return cls(**scenario.model_dump(), stratum=stratum)


def stratified_sample(
    scenarios: list[ComplianceScenario],
    *,
    per_depth: int,
    mode_floor: int,
    seed: int,
) -> list[SampledScenario]:
    """`per_depth` from every rule depth, then top up any mode below the floor.

    Pools are sorted by `scenario_id` before drawing, so the sample depends on
    the seed and not on the order the suite happens to sit in on disk. Without
    that sort, regenerating the suite in a different order would silently
    produce a different sample under the same seed -- reproducibility that
    reads as reproducible and is not.

    A request larger than the pool draws the whole pool and says so. Design
    §10 offers both numbers as flags for widening the sample, and both have a
    ceiling: depth 4 holds 350 scenarios and `unmet_aos` 50, so
    `--per-depth 400` would quietly produce a "core" that is not equally
    allocated -- the one property §4 says the core has -- and `--mode-floor 60`
    a mode that never reaches its floor. Either costs power rather than
    introducing bias, which is why this is a printed note and not an error.
    """
    rng = random.Random(seed)

    def note(stratum: str, asked: int, got: int) -> None:
        """Say, once, that a stratum could not be given what was asked for."""
        print(
            f"  -- {stratum}: {asked} asked for, {got} drawn -- the suite has "
            f"no more. The stratum is short, so it carries less power than the "
            f"others; which scenarios it holds is unchanged."
        )

    by_depth: dict[int, list[ComplianceScenario]] = defaultdict(list)
    for scenario in scenarios:
        by_depth[scenario.rule_depth].append(scenario)

    core: list[ComplianceScenario] = []
    for depth in sorted(by_depth):
        pool = sorted(by_depth[depth], key=lambda s: s.scenario_id)
        if per_depth > len(pool):
            note(f"depth {depth}", per_depth, len(pool))
        core.extend(rng.sample(pool, min(per_depth, len(pool))))

    counts = Counter(scenario.failure_mode for scenario in core)
    chosen = {scenario.scenario_id for scenario in core}

    top_up: list[ComplianceScenario] = []
    for mode in sorted({scenario.failure_mode for scenario in scenarios}):
        shortfall = mode_floor - counts.get(mode, 0)
        if shortfall <= 0:
            continue
        pool = sorted(
            (
                scenario
                for scenario in scenarios
                if scenario.failure_mode == mode and scenario.scenario_id not in chosen
            ),
            key=lambda s: s.scenario_id,
        )
        drawn = rng.sample(pool, min(shortfall, len(pool)))
        if len(drawn) < shortfall:
            note(mode, mode_floor, counts.get(mode, 0) + len(drawn))
        top_up.extend(drawn)
        chosen.update(scenario.scenario_id for scenario in drawn)

    return [SampledScenario.of(s, "core") for s in core] + [
        SampledScenario.of(s, "top_up") for s in top_up
    ]


def sample_digest(sampled: list[SampledScenario]) -> str:
    """A stable identifier for *which* scenarios were drawn.

    Sorted before hashing, so the digest identifies the set and not the order
    -- the manifest uses it to refuse a campaign resumed under a different
    sample, and a resume must not fail merely because the sample was written
    in a different order.
    """
    ids = sorted(scenario.scenario_id for scenario in sampled)
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


def write_sample(campaign_dir, sampled: list[SampledScenario]) -> Path:
    """Record the drawn sample beside the runs it produced.

    Without this file the scoring subsystem would have to re-derive the
    sample, and could re-derive a different one.
    """
    campaign_dir = Path(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_dir / SAMPLE
    lines = [
        json.dumps(scenario.model_dump(), ensure_ascii=False) for scenario in sampled
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def read_sample(campaign_dir) -> list[SampledScenario]:
    path = Path(campaign_dir) / SAMPLE
    if not path.exists():
        return []
    return [
        SampledScenario.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
