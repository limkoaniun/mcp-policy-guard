"""Resume and replacement -- the two rules that decide what actually runs.

No proxy and no model here: `runner` is injected, so every path including the
ones that are hard to provoke with real infrastructure is deterministic.
"""

import pytest

from enrolment.agent.campaign import (
    IndexRecord,
    RunSpec,
    read_index,
    run_campaign,
    write_index,
)
from enrolment.agent.transcript import ResponseOutcome, Transcript, TurnRecord


def describe(spec):
    return {
        "scenario_id": spec.scenario_id, "attack_class": None, "model": spec.model,
        "arm": spec.arm, "repeat": spec.repeat, "attempt": spec.attempt,
    }


def spec(scenario_id="SC-07", model="fake", arm="on", repeat=1):
    return RunSpec(scenario_id=scenario_id, model=model, arm=arm, repeat=repeat)


def recording_runner(statuses):
    """A runner returning a scripted status per run_id, defaulting to ok."""
    seen = []

    async def runner(s):
        seen.append(s.run_id)
        return statuses.get(s.run_id, "ok"), 0.5

    return runner, seen


def write_transcript(campaign_dir, run_id, *, truncated=False, provider_error=False):
    turns = []
    if provider_error:
        turns = [TurnRecord(
            user_turn=0, model_turn=0,
            outcome=ResponseOutcome.PROVIDER_ERROR, text="TimeoutError",
        )]
    (campaign_dir / run_id).mkdir(parents=True, exist_ok=True)
    Transcript(
        task_id=run_id, model="fake",
        truncated=truncated or provider_error,
        truncation_reason="reason" if truncated or provider_error else None,
        turns=turns,
    ).write_jsonl(campaign_dir / run_id / "transcript.jsonl")


@pytest.mark.anyio
async def test_every_spec_runs_once_on_a_fresh_campaign(tmp_path):
    runner, seen = recording_runner({})
    records = await run_campaign(
        [spec(repeat=1), spec(repeat=2)], runner,
        campaign_dir=tmp_path, describe=describe,
    )
    assert seen == ["SC-07-fake-on-r1", "SC-07-fake-on-r2"]
    assert [r.status for r in records] == ["ok", "ok"]


@pytest.mark.anyio
async def test_a_completed_run_is_skipped_on_resume(tmp_path):
    write_transcript(tmp_path, "SC-07-fake-on-r1")
    runner, seen = recording_runner({})
    records = await run_campaign(
        [spec(repeat=1), spec(repeat=2)], runner,
        campaign_dir=tmp_path, describe=describe,
    )
    assert seen == ["SC-07-fake-on-r2"]
    assert len(records) == 2
    assert records[0].status == "ok"


@pytest.mark.anyio
async def test_a_truncated_run_is_skipped_and_never_replaced(tmp_path):
    """Spec §8: a bound being reached is a fact about the model's behaviour
    and a result in its own right."""
    write_transcript(tmp_path, "SC-07-fake-on-r1", truncated=True)
    runner, seen = recording_runner({})
    records = await run_campaign([spec()], runner, campaign_dir=tmp_path, describe=describe)
    assert seen == []
    assert [r.status for r in records] == ["truncated"]
    assert records[0].replaced_by is None


@pytest.mark.anyio
async def test_a_provider_error_is_replaced_and_both_are_recorded(tmp_path):
    runner, seen = recording_runner({"SC-07-fake-on-r1": "provider_error"})
    records = await run_campaign([spec()], runner, campaign_dir=tmp_path, describe=describe)

    assert seen == ["SC-07-fake-on-r1", "SC-07-fake-on-r1a"]
    by_id = {r.run_id: r for r in records}
    assert by_id["SC-07-fake-on-r1"].status == "provider_error"
    assert by_id["SC-07-fake-on-r1"].replaced_by == "SC-07-fake-on-r1a"
    assert by_id["SC-07-fake-on-r1a"].replaces == "SC-07-fake-on-r1"
    assert by_id["SC-07-fake-on-r1a"].status == "ok"


@pytest.mark.anyio
async def test_replacement_stops_at_the_bound(tmp_path):
    """Spec §8: at most two attempts, then the cell is left short and D
    reports the reduced N rather than B trying harder."""
    runner, seen = recording_runner({
        "SC-07-fake-on-r1": "provider_error",
        "SC-07-fake-on-r1a": "provider_error",
        "SC-07-fake-on-r1b": "provider_error",
    })
    records = await run_campaign([spec()], runner, campaign_dir=tmp_path, describe=describe)

    assert seen == ["SC-07-fake-on-r1", "SC-07-fake-on-r1a", "SC-07-fake-on-r1b"]
    assert len(records) == 3
    assert records[-1].replaced_by is None


@pytest.mark.anyio
async def test_a_provider_error_found_on_resume_triggers_its_replacement(tmp_path):
    """Spec §9: a provider_error run on disk is not skipped -- it is the
    trigger for the replacement."""
    write_transcript(tmp_path, "SC-07-fake-on-r1", provider_error=True)
    runner, seen = recording_runner({})
    records = await run_campaign([spec()], runner, campaign_dir=tmp_path, describe=describe)

    assert seen == ["SC-07-fake-on-r1a"]
    assert {r.run_id for r in records} == {"SC-07-fake-on-r1", "SC-07-fake-on-r1a"}


@pytest.mark.anyio
async def test_the_index_is_on_disk_after_every_run(tmp_path):
    """A campaign killed mid-flight must still leave a consistent index."""
    runner, _ = recording_runner({})
    await run_campaign([spec(repeat=1), spec(repeat=2)], runner,
                       campaign_dir=tmp_path, describe=describe)
    assert len(read_index(tmp_path)) == 2


@pytest.mark.anyio
async def test_an_adopted_run_keeps_the_duration_that_was_measured(tmp_path):
    """A resumed run must not be indexed with a duration that never happened.

    `duration_s` is read by the scoring subsystem. Re-deriving every prior row
    at zero on every resume -- and design §9 says the campaign *will* be
    resumed -- would put a fabricated measurement in the artifact D reads,
    the same species of bug `ollama._provider_latency_ms` exists to refuse.
    """
    write_transcript(tmp_path, "SC-07-fake-on-r1")
    write_index(tmp_path, [
        IndexRecord(run_id="SC-07-fake-on-r1", status="ok", duration_s=41.5,
                    **describe(spec(repeat=1))),
    ])

    runner, seen = recording_runner({})
    records = await run_campaign(
        [spec(repeat=1), spec(repeat=2)], runner,
        campaign_dir=tmp_path, describe=describe,
    )

    assert seen == ["SC-07-fake-on-r2"]
    assert {r.run_id: r.duration_s for r in records} == {
        "SC-07-fake-on-r1": 41.5, "SC-07-fake-on-r2": 0.5,
    }


@pytest.mark.anyio
async def test_an_adopted_run_with_no_prior_row_is_untimed_not_instant(tmp_path):
    """Nothing measured this run, so nothing here claims a measurement."""
    write_transcript(tmp_path, "SC-07-fake-on-r1")
    runner, seen = recording_runner({})

    records = await run_campaign([spec()], runner, campaign_dir=tmp_path, describe=describe)

    assert seen == []
    assert records[0].duration_s is None
    assert read_index(tmp_path)[0].duration_s is None


@pytest.mark.anyio
async def test_a_short_spec_list_does_not_delete_the_rows_it_omits(tmp_path):
    """`--limit` is a cap on one invocation, not a claim about the campaign.

    The index is rewritten whole, so deriving it from this invocation's specs
    alone deleted the row of every already-completed run past the cap. The run
    directories survive, but the index is what the scoring subsystem reads.
    """
    runner, _ = recording_runner({})
    await run_campaign(
        [spec(repeat=1), spec(repeat=2), spec(repeat=3)], runner,
        campaign_dir=tmp_path, describe=describe,
    )
    for repeat in (1, 2, 3):
        write_transcript(tmp_path, f"SC-07-fake-on-r{repeat}")

    capped, seen = recording_runner({})
    await run_campaign([spec(repeat=1)], capped, campaign_dir=tmp_path, describe=describe)

    assert seen == []
    assert {r.run_id for r in read_index(tmp_path)} == {
        "SC-07-fake-on-r1", "SC-07-fake-on-r2", "SC-07-fake-on-r3",
    }
    assert [r.duration_s for r in read_index(tmp_path)] == [0.5, 0.5, 0.5]


@pytest.mark.anyio
async def test_a_replacement_already_on_disk_is_adopted_not_re_run(tmp_path):
    """Design §8 and §9 together, on the state a resumed campaign actually has.

    A `provider_error` on disk is deliberately not skipped -- it is the
    trigger for its own replacement. But if that replacement already ran, it
    must be adopted like any other completed run, or every resume of a
    campaign that ever hit a dropped connection would re-run the answer to it.
    """
    write_transcript(tmp_path, "SC-07-fake-on-r1", provider_error=True)
    write_transcript(tmp_path, "SC-07-fake-on-r1a")
    write_index(tmp_path, [
        IndexRecord(run_id="SC-07-fake-on-r1", status="provider_error",
                    duration_s=3.5, replaced_by="SC-07-fake-on-r1a",
                    **describe(spec())),
        IndexRecord(run_id="SC-07-fake-on-r1a", status="ok", duration_s=12.25,
                    replaces="SC-07-fake-on-r1",
                    **describe(spec().replacement())),
    ])

    runner, seen = recording_runner({})
    records = await run_campaign([spec()], runner, campaign_dir=tmp_path, describe=describe)

    assert seen == []
    by_id = {r.run_id: r for r in records}
    assert by_id["SC-07-fake-on-r1"].duration_s == 3.5
    assert by_id["SC-07-fake-on-r1a"].duration_s == 12.25
    assert by_id["SC-07-fake-on-r1"].replaced_by == "SC-07-fake-on-r1a"
    assert by_id["SC-07-fake-on-r1a"].replaces == "SC-07-fake-on-r1"


@pytest.mark.anyio
async def test_a_runner_that_raises_ends_the_campaign_rather_than_hiding_it(tmp_path):
    """A RunDirectoryInUseError or a FileExistsError means the resume logic is
    wrong. Swallowing it would produce the log-and-state divergence RunState
    raises to prevent."""
    async def exploding(s):
        raise FileExistsError("run dir already exists")

    with pytest.raises(FileExistsError):
        await run_campaign([spec()], exploding, campaign_dir=tmp_path, describe=describe)
