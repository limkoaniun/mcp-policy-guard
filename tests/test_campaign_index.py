"""The campaign's own bookkeeping: what was run, under what method."""

import json

import pytest

from enrolment.agent.campaign import (
    CampaignManifest,
    CampaignMismatchError,
    IndexRecord,
    load_or_create_manifest,
    read_index,
    write_index,
)


def manifest(**overrides):
    base = {
        "models": ["llama3.1:8b", "llama3.2"], "repeats": 5,
        "system_prompt": "You help {principal}.", "nudge": "Thanks. Please proceed.",
        "policy_pack": "configs/enrolment-personas.yaml",
    }
    base.update(overrides)
    return CampaignManifest(**base)


def record(run_id, **overrides):
    base = {
        "run_id": run_id, "scenario_id": "SC-07", "attack_class": "exfiltration",
        "model": "llama3.2", "arm": "on", "repeat": 1, "status": "ok", "duration_s": 1.5,
    }
    base.update(overrides)
    return IndexRecord(**base)


def test_the_index_round_trips(tmp_path):
    records = [record("SC-07-llama32-on-r1"), record("SC-07-llama32-on-r2", repeat=2)]
    write_index(tmp_path, records)
    assert read_index(tmp_path) == records


def test_the_index_is_rewritten_whole_not_appended(tmp_path):
    """Spec §8 links a replacement in both directions, which means an earlier
    record gains a `replaced_by` after it was first written. Append-only
    would leave the stale copy behind."""
    write_index(tmp_path, [record("A"), record("B")])
    write_index(tmp_path, [record("A", replaced_by="Aa"), record("Aa", replaces="A")])
    ids = [r.run_id for r in read_index(tmp_path)]
    assert ids == ["A", "Aa"]
    assert read_index(tmp_path)[0].replaced_by == "Aa"


def test_reading_an_absent_index_gives_nothing(tmp_path):
    assert read_index(tmp_path) == []


def test_a_new_campaign_writes_its_manifest(tmp_path):
    stored = load_or_create_manifest(tmp_path, manifest())
    assert (tmp_path / "campaign.json").exists()
    assert stored.repeats == 5
    assert json.loads((tmp_path / "campaign.json").read_text())["nudge"]


def test_resuming_under_the_same_method_returns_the_stored_manifest(tmp_path):
    load_or_create_manifest(tmp_path, manifest())
    stored = load_or_create_manifest(tmp_path, manifest())
    assert stored.system_prompt == "You help {principal}."


def test_resuming_under_a_different_system_prompt_is_refused(tmp_path):
    """Spec §10: half a campaign run under one method and half under another
    is not a campaign."""
    load_or_create_manifest(tmp_path, manifest())
    with pytest.raises(CampaignMismatchError) as error:
        load_or_create_manifest(tmp_path, manifest(system_prompt="Different."))
    assert "system_prompt" in str(error.value)


def test_resuming_under_a_different_model_list_is_refused(tmp_path):
    load_or_create_manifest(tmp_path, manifest())
    with pytest.raises(CampaignMismatchError) as error:
        load_or_create_manifest(tmp_path, manifest(models=["llama3.2"]))
    assert "models" in str(error.value)


def test_a_refreshed_fingerprint_does_not_invalidate_a_resume(tmp_path):
    """A digest is observed, not chosen. Re-reading it from the daemon must
    not read as a change of method."""
    load_or_create_manifest(tmp_path, manifest(fingerprints={"llama3.2": {"digest": "a"}}))
    stored = load_or_create_manifest(tmp_path, manifest(fingerprints={"llama3.2": {"digest": "b"}}))
    assert stored is not None
