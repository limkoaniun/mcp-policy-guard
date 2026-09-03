"""The sampling design IS subsystem C.

Design §4: equal allocation per depth, because a cliff must be locatable and
locating one needs equal power either side of wherever it falls. Design §5: a
floor per failure mode, because a rate computed on one scenario is not a rate.
Design §5 also fixes the consequence -- the top-ups unbalance the depths, so
the depth curve is the core and per-mode rates are the whole.
"""

import collections
import json
import pathlib

import pytest

from enrolment.agent.sampling import (
    SampledScenario,
    read_sample,
    sample_digest,
    stratified_sample,
    write_sample,
)
from enrolment.scenarios import ComplianceScenario, read_jsonl

REPO = pathlib.Path(__file__).resolve().parents[1]
SUITE = REPO / "data" / "compliance_scenarios.jsonl"

PER_DEPTH = 40
MODE_FLOOR = 25
SEED = 20260903


def suite():
    return read_jsonl(SUITE)


def drawn(**overrides):
    kwargs = {"per_depth": PER_DEPTH, "mode_floor": MODE_FLOOR, "seed": SEED}
    kwargs.update(overrides)
    return stratified_sample(suite(), **kwargs)


def test_the_core_is_equally_allocated_across_depth():
    """Design §4. Proportional sampling would put the thinnest sample at
    depth 4, where a failure is most likely and the estimate most needs
    power."""
    core = [s for s in drawn() if s.stratum == "core"]
    counts = collections.Counter(s.rule_depth for s in core)
    assert set(counts) == {0, 1, 2, 3, 4}
    assert set(counts.values()) == {PER_DEPTH}


def test_every_failure_mode_reaches_the_floor():
    """Design §5: 40-per-depth draws ~1.3 unmet_aos out of 200, and a rate
    computed on one scenario is not a rate."""
    counts = collections.Counter(s.failure_mode for s in drawn())
    for mode, n in counts.items():
        assert n >= MODE_FLOOR, f"{mode} has {n}, below the floor"


def test_the_sample_is_the_size_the_design_measured():
    """Design's Global Constraints record 248 at these parameters, derived by
    running against the real suite before the design was written."""
    sampled = drawn()
    assert len(sampled) == 248
    assert len([s for s in sampled if s.stratum == "core"]) == 200
    assert len([s for s in sampled if s.stratum == "top_up"]) == 48


def test_the_top_ups_are_the_modes_the_design_named():
    top_up = collections.Counter(
        s.failure_mode for s in drawn() if s.stratum == "top_up"
    )
    assert dict(top_up) == {
        "unmet_admission": 3,
        "unmet_aos": 24,
        "unmet_credit_points": 21,
    }


def test_the_full_sample_is_deliberately_not_depth_balanced():
    """Design §5: this is why the depth curve is the core and per-mode rates
    are the whole. A test asserting the full sample IS balanced would be
    asserting the bug."""
    counts = collections.Counter(s.rule_depth for s in drawn())
    assert dict(sorted(counts.items())) == {0: 40, 1: 40, 2: 65, 3: 63, 4: 40}


def test_no_scenario_is_drawn_twice():
    sampled = drawn()
    assert len({s.scenario_id for s in sampled}) == len(sampled)


def test_the_sample_is_deterministic_under_a_seed():
    assert [s.scenario_id for s in drawn()] == [s.scenario_id for s in drawn()]


def test_a_different_seed_draws_a_different_sample():
    assert [s.scenario_id for s in drawn()] != [s.scenario_id for s in drawn(seed=1)]


def test_the_sample_does_not_depend_on_the_order_of_the_input():
    """Reproducibility must survive the suite being regenerated in a different
    order. Without an internal sort, `random.sample` over a reordered list
    returns different scenarios under the same seed."""
    forwards = stratified_sample(
        suite(), per_depth=PER_DEPTH, mode_floor=MODE_FLOOR, seed=SEED
    )
    backwards = stratified_sample(
        list(reversed(suite())), per_depth=PER_DEPTH, mode_floor=MODE_FLOOR, seed=SEED
    )
    assert [s.scenario_id for s in forwards] == [s.scenario_id for s in backwards]


def tiny_suite(n=3, **overrides):
    base = {
        "student_id": "S1", "failure_mode": "compliant", "rule_depth": 0,
        "expected_satisfied": True, "explanation": "",
    }
    base.update(overrides)
    return [
        ComplianceScenario(scenario_id=f"S-{i}", subject_code=str(i), **base)
        for i in range(n)
    ]


def test_a_depth_with_fewer_scenarios_than_asked_for_gives_what_it_has():
    sampled = stratified_sample(tiny_suite(), per_depth=10, mode_floor=0, seed=SEED)
    assert len(sampled) == 3


def test_a_depth_short_of_per_depth_says_so(capsys):
    """Design §10 offers --per-depth as the flag for widening the sample, and
    depth 4 holds only 350 scenarios. Asking for more silently produces an
    unequally allocated "core" -- which is the one property §4 says the core
    has. It costs power rather than introducing bias, so it is a note and not
    an error, but it must not be silent."""
    stratified_sample(tiny_suite(), per_depth=10, mode_floor=0, seed=SEED)
    printed = capsys.readouterr().out
    assert "depth 0" in printed
    assert "10" in printed and "3" in printed


def test_a_mode_that_cannot_reach_the_floor_says_so(capsys):
    """--mode-floor 60 against 50 unmet_aos yields 50, and a per-mode rate is
    then computed on a denominator nobody asked for."""
    stratified_sample(
        tiny_suite(failure_mode="unmet_aos", expected_satisfied=False),
        per_depth=1, mode_floor=9, seed=SEED,
    )
    printed = capsys.readouterr().out
    assert "unmet_aos" in printed
    assert "9" in printed


def test_a_sample_that_meets_every_request_says_nothing(capsys):
    """The note is a warning, not a running commentary."""
    drawn()
    assert capsys.readouterr().out == ""


def test_every_ground_truth_field_survives_into_the_sample():
    """`SampledScenario.of` splats `scenario.model_dump()`, and pydantic's
    default `extra="ignore"` means a field added to ComplianceScenario later
    would vanish from sample.jsonl -- the file subsystem D depends on -- with
    no error anywhere."""
    assert set(ComplianceScenario.model_fields) <= set(SampledScenario.model_fields)


def test_the_digest_identifies_the_sample_not_its_order():
    """The manifest carries this so a campaign resumed under a different
    sample is refused. It must not change when the same scenarios arrive in a
    different order, or a resume would fail for no reason."""
    sampled = drawn()
    assert sample_digest(sampled) == sample_digest(list(reversed(sampled)))
    assert sample_digest(sampled) != sample_digest(sampled[:-1])


def test_the_sample_round_trips_through_the_campaign_directory(tmp_path):
    sampled = drawn()
    write_sample(tmp_path, sampled)
    assert read_sample(tmp_path) == sampled


def test_the_written_sample_carries_the_ground_truth_d_needs(tmp_path):
    """Design §8: without this file D would have to re-derive the sample, and
    could re-derive a different one."""
    write_sample(tmp_path, drawn())
    first = json.loads((tmp_path / "sample.jsonl").read_text().splitlines()[0])
    for field in (
        "scenario_id", "student_id", "subject_code", "rule_depth",
        "failure_mode", "expected_satisfied", "explanation", "stratum",
    ):
        assert field in first


def test_reading_an_absent_sample_gives_nothing(tmp_path):
    assert read_sample(tmp_path) == []


def test_a_sampled_scenario_keeps_every_field_of_the_original():
    original = {s.scenario_id: s for s in suite()}
    for s in drawn()[:20]:
        source = original[s.scenario_id]
        assert s.student_id == source.student_id
        assert s.subject_code == source.subject_code
        assert s.rule_depth == source.rule_depth
        assert s.failure_mode == source.failure_mode
        assert s.expected_satisfied == source.expected_satisfied
        assert s.explanation == source.explanation
        assert s.stratum in {"core", "top_up"}


def test_sampled_scenario_rejects_an_unknown_stratum():
    with pytest.raises(ValueError):
        SampledScenario(
            scenario_id="x", student_id="S1", subject_code="1",
            failure_mode="compliant", rule_depth=0, expected_satisfied=True,
            explanation="", stratum="invented",
        )
