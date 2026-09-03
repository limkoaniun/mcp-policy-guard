"""Argument handling, the dry run, and the wiring main() puts together."""

import json

import pytest

from enrolment.agent import compliance
from enrolment.agent.compliance import parse_args


def test_the_defaults_are_the_sample_the_design_specifies():
    args = parse_args(["--campaign", "x"])
    assert args.models == ["llama3.1:8b", "llama3.2"]
    assert args.repeats == 5
    assert args.per_depth == 40
    assert args.mode_floor == 25
    assert args.sample_seed == 20260903


def test_models_are_a_comma_separated_list():
    args = parse_args(["--campaign", "x", "--models", "a,b"])
    assert args.models == ["a", "b"]


def test_a_dry_run_reports_both_denominators(capsys):
    """Design §5: the depth curve is the core, per-mode rates are the whole.
    A dry run that reported only one total would invite the error the stratum
    label exists to prevent."""
    assert compliance.main(["--campaign", "x", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "248" in printed
    assert "200" in printed
    assert "4960" in printed


def test_a_dry_run_names_the_per_depth_composition(capsys):
    compliance.main(["--campaign", "x", "--dry-run"])
    printed = capsys.readouterr().out
    for depth in range(5):
        assert f"depth {depth}" in printed


def test_a_dry_run_states_that_depth_zero_cannot_produce_a_violation(capsys):
    """Design §11: a zero violation rate at depth 0 is a property of the
    suite, not a finding about the model, and the tool that prints the sample
    should say so."""
    compliance.main(["--campaign", "x", "--dry-run"])
    assert "compliant" in capsys.readouterr().out.lower()


def test_a_dry_run_touches_no_network_and_writes_nothing(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("preflight must not run for a dry run")

    monkeypatch.setattr(compliance, "preflight", explode)
    assert compliance.main(["--campaign", str(tmp_path / "c"), "--dry-run"]) == 0
    assert not (tmp_path / "c").exists()


def test_main_wires_the_campaign_together(tmp_path, monkeypatch):
    """The path that has no other coverage: build_manifest -> chat_factory ->
    runner -> describe -> the index."""
    seen = []

    async def fake_run_one(scenario, spec, **kwargs):
        seen.append(spec.run_id)
        from enrolment.agent.transcript import Transcript
        return Transcript(task_id=spec.scenario_id, model=spec.model), 1.25

    monkeypatch.setattr(
        compliance, "preflight",
        lambda models, **k: {m: {"model": m, "digest": "sha256:x"} for m in models},
    )
    monkeypatch.setattr(compliance, "run_one", fake_run_one)
    monkeypatch.setattr(compliance, "ollama_chat", lambda m, **k: (lambda a, b: None))

    exit_code = compliance.main([
        "--campaign", str(tmp_path / "c"), "--models", "m",
        "--repeats", "1", "--per-depth", "1", "--mode-floor", "0", "--limit", "2",
    ])

    assert exit_code == 0
    assert len(seen) == 2
    index = [
        json.loads(line)
        for line in (tmp_path / "c" / "index.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(index) == 2
    assert {r["arm"] for r in index} == {"on", "off"}
    assert all(r["duration_s"] == 1.25 for r in index)


def test_main_writes_the_sample_and_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        compliance, "preflight",
        lambda models, **k: {m: {"model": m, "digest": "sha256:x"} for m in models},
    )

    async def fake_run_one(scenario, spec, **kwargs):
        from enrolment.agent.transcript import Transcript
        return Transcript(task_id=spec.scenario_id, model=spec.model), 1.0

    monkeypatch.setattr(compliance, "run_one", fake_run_one)
    monkeypatch.setattr(compliance, "ollama_chat", lambda m, **k: (lambda a, b: None))

    compliance.main([
        "--campaign", str(tmp_path / "c"), "--models", "m",
        "--repeats", "1", "--per-depth", "1", "--mode-floor", "0", "--limit", "1",
    ])

    campaign = tmp_path / "c"
    assert (campaign / "sample.jsonl").exists()
    stored = json.loads((campaign / "campaign.json").read_text())
    assert stored["sample_digest"]
    first = json.loads((campaign / "sample.jsonl").read_text().splitlines()[0])
    assert first["stratum"] in {"core", "top_up"}
    assert "expected_satisfied" in first


def test_resuming_under_a_different_sample_is_refused(tmp_path, monkeypatch):
    """Design §8: half a campaign over one sample and half over another is not
    a campaign."""
    from enrolment.agent.campaign import CampaignMismatchError

    monkeypatch.setattr(
        compliance, "preflight",
        lambda models, **k: {m: {"model": m, "digest": "sha256:x"} for m in models},
    )

    async def fake_run_one(scenario, spec, **kwargs):
        from enrolment.agent.transcript import Transcript
        return Transcript(task_id=spec.scenario_id, model=spec.model), 1.0

    monkeypatch.setattr(compliance, "run_one", fake_run_one)
    monkeypatch.setattr(compliance, "ollama_chat", lambda m, **k: (lambda a, b: None))

    base = ["--campaign", str(tmp_path / "c"), "--models", "m", "--repeats", "1",
            "--mode-floor", "0", "--limit", "1"]
    compliance.main([*base, "--per-depth", "1"])
    with pytest.raises(CampaignMismatchError) as error:
        compliance.main([*base, "--per-depth", "2"])
    assert "sample_digest" in str(error.value)
