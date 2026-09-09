"""Deriving an ablated policy pack.

Spec §7 asks for each policy turned off in turn. The pack is the single source
of truth for which policies load, so an ablation is a pack with one name
removed -- derived from the real pack rather than hand-copied, because a
hand-copied pack that drifts from the original ablates against a baseline
nobody ran.
"""

import pathlib

import pytest
import yaml

from enrolment.ablation import ablated_pack

REPO = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO / "configs" / "enrolment-personas.yaml"


def test_it_removes_only_the_named_policy(tmp_path):
    derived = ablated_pack(PACK, "allowlist", tmp_path)
    body = yaml.safe_load(derived.read_text(encoding="utf-8"))

    assert body["policies"] == ["arg_validation"]


def test_it_keeps_everything_else_the_pack_declares(tmp_path):
    """Upstream command, trust labels and domain rules must survive: an
    ablation that also changed where the data lives would not be an ablation."""
    original = yaml.safe_load(PACK.read_text(encoding="utf-8"))
    derived = yaml.safe_load(ablated_pack(PACK, "allowlist", tmp_path).read_text())

    for key in ("upstream_command", "upstream_args", "trust", "domain_rules",
                "domain_rules_args", "principal_argument", "principal_exempt_tools"):
        assert derived[key] == original[key], key


def test_the_derived_pack_names_the_ablation(tmp_path):
    """A run's manifest records the pack's name, so the name has to say which
    arm produced it -- otherwise the ablation and the baseline are told apart
    only by directory."""
    derived = ablated_pack(PACK, "allowlist", tmp_path)
    body = yaml.safe_load(derived.read_text(encoding="utf-8"))

    assert "allowlist" in body["name"]
    assert body["name"] != yaml.safe_load(PACK.read_text())["name"]


def test_ablating_a_policy_the_pack_does_not_load_is_refused(tmp_path):
    """Silently producing an identical pack would report a baseline as an
    ablation."""
    with pytest.raises(ValueError, match="taint"):
        ablated_pack(PACK, "taint", tmp_path)


def test_ablating_the_last_policy_is_refused(tmp_path):
    """`policy_guard.__main__` already refuses an enforcement-on run with an
    empty engine, because it allows every call while logging verdicts that
    look like enforcement. Catching it here names the ablation that caused it."""
    once = ablated_pack(PACK, "allowlist", tmp_path)
    with pytest.raises(ValueError, match="no policies"):
        ablated_pack(once, "arg_validation", tmp_path / "again")


def test_the_adversarial_cli_runs_against_the_ablated_pack(tmp_path, capsys):
    """`--ablate` derives the pack into the campaign directory, so the pack a
    run used is stored beside the runs it produced."""
    from enrolment.agent.adversarial import main

    main([
        "--campaign", str(tmp_path / "camp"), "--models", "fake",
        "--ablate", "allowlist", "--dry-run",
    ])

    derived = tmp_path / "camp" / "enrolment-personas-no-allowlist.yaml"
    assert derived.exists()
    assert yaml.safe_load(derived.read_text())["policies"] == ["arg_validation"]
    assert "allowlist" in capsys.readouterr().out


def test_the_compliance_cli_runs_against_the_ablated_pack(tmp_path, capsys):
    from enrolment.agent.compliance import main

    main([
        "--campaign", str(tmp_path / "camp"), "--models", "fake",
        "--ablate", "arg_validation", "--dry-run",
    ])

    derived = tmp_path / "camp" / "enrolment-no-arg_validation.yaml"
    assert derived.exists()
    assert yaml.safe_load(derived.read_text())["policies"] == ["allowlist"]
