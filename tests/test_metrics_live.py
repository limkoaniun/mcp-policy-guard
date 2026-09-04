"""Scoring a campaign that a real model actually produced.

Skipped loudly when no campaign is on disk: a test that proves nothing when it
does not run must not look like a pass.
"""

import json
import pathlib

import pytest

from enrolment.metrics import report
from enrolment.metrics.aggregate import aggregate

REPO = pathlib.Path(__file__).resolve().parents[1]
CAMPAIGN = REPO / "experiments" / "runs" / "adversarial" / "first"

LIVE = pytest.mark.skipif(
    not (CAMPAIGN / "index.jsonl").exists(),
    reason=(
        f"no campaign at {CAMPAIGN} -- run "
        f"`python -m enrolment.agent.adversarial --campaign {CAMPAIGN}` first. "
        f"Skipped, not passed: this test proves nothing when it does not run."
    ),
)


@LIVE
def test_a_real_campaign_scores_end_to_end(tmp_path):
    verdicts = report.score_campaign(CAMPAIGN)
    assert verdicts, "the campaign's index named no runs"

    figures = aggregate(verdicts)
    assert figures["total"] == len(verdicts)
    assert figures["scored"] + sum(figures["exclusions"].values()) == figures["total"]

    # Every verdict must be decidable: scoreable, or excluded for a declared
    # reason. A verdict that is neither is a gap in the exclusion table.
    for verdict in verdicts:
        assert verdict.scoreable or verdict.excluded_because

    # The undeliverable classes must report absence, never a zero rate.
    for cls in ("tool-shadowing", "poisoned-description"):
        if cls in figures["attack_success"]:
            assert figures["attack_success"][cls]["off"]["denominator"] == 0

    print("\n" + json.dumps(figures, indent=2)[:2000])
