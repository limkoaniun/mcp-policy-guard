"""Argument handling and the preflight. No daemon, no runs."""

import urllib.error

import pytest

import enrolment.agent.ollama as ollama_module
from enrolment.agent import adversarial
from enrolment.agent.adversarial import parse_args, preflight


def test_models_are_a_comma_separated_list():
    args = parse_args(["--campaign", "x", "--models", "llama3.1:8b,llama3.2"])
    assert args.models == ["llama3.1:8b", "llama3.2"]


def test_the_defaults_are_the_matrix_the_spec_states():
    args = parse_args(["--campaign", "x"])
    assert args.models == ["llama3.1:8b", "llama3.2"]
    assert args.repeats == 5


def test_a_dry_run_reports_the_matrix_without_running_it(capsys):
    exit_code = adversarial.main(["--campaign", "x", "--dry-run"])
    printed = capsys.readouterr().out
    assert exit_code == 0
    assert "280" in printed
    assert "160" in printed and "120" in printed


def test_a_dry_run_names_what_has_no_off_arm(capsys):
    """Spec §6: the hole in the results table is reported, not omitted."""
    adversarial.main(["--campaign", "x", "--dry-run"])
    printed = capsys.readouterr().out
    for sid in ("SC-01", "SC-03", "SC-05", "SC-08"):
        assert sid in printed


def test_limit_caps_what_one_invocation_runs():
    args = parse_args(["--campaign", "x", "--limit", "1"])
    assert args.limit == 1


def test_preflight_names_the_model_it_could_not_find(monkeypatch):
    """Spec §10: a campaign that cannot reach its models fails immediately,
    rather than producing 280 failed runs."""
    monkeypatch.setattr(
        adversarial, "model_fingerprint",
        lambda model, **kwargs: {"model": model, "digest": "unknown"},
    )
    with pytest.raises(RuntimeError) as error:
        preflight(["llama3.2"])
    assert "llama3.2" in str(error.value)
    assert "ollama pull" in str(error.value)


def test_preflight_names_ollama_serve_when_the_daemon_is_unreachable(monkeypatch):
    """Spec §10 names one failure mode: "Ollama unreachable, *or* the model
    not pulled." Only the second half was handled; the first escaped as a raw
    URLError out of the HTTP layer -- a stack trace where the design promises
    a message the researcher can act on.
    """
    def refuse(model, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))

    monkeypatch.setattr(adversarial, "model_fingerprint", refuse)

    with pytest.raises(RuntimeError) as error:
        preflight(["llama3.2"])

    assert "ollama serve" in str(error.value)
    assert "llama3.2" in str(error.value)


def test_preflight_accepts_a_bare_name_the_daemon_tags_as_latest(monkeypatch):
    """The regression Critical 1 named, at the layer that aborted the campaign."""
    tags = {"models": [{"name": "llama3.2:latest", "digest": "a80c4f17acd5"}]}
    monkeypatch.setattr(
        ollama_module, "_get",
        lambda url, timeout: {"version": "0.33.2"} if url.endswith("version") else tags,
    )
    monkeypatch.setattr(adversarial, "warm_up", lambda model, **kwargs: None)

    fingerprints = preflight(["llama3.2"])

    assert fingerprints["llama3.2"]["digest"] == "a80c4f17acd5"


def test_preflight_warms_every_model_before_any_measured_run(monkeypatch):
    """A's §7: llama3.2's first response after a load differed from those that
    followed. A measured run should not be the one that is different for a
    reason unrelated to the experiment."""
    warmed = []
    monkeypatch.setattr(
        adversarial, "model_fingerprint",
        lambda model, **kwargs: {"model": model, "digest": "sha256:x"},
    )
    monkeypatch.setattr(adversarial, "warm_up", lambda model, **kwargs: warmed.append(model))

    fingerprints = preflight(["llama3.1:8b", "llama3.2"])

    assert warmed == ["llama3.1:8b", "llama3.2"]
    assert fingerprints["llama3.2"]["digest"] == "sha256:x"
