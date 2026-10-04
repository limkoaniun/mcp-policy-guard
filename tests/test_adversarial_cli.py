"""Argument handling and the preflight. No daemon, no runs."""

import json
import pathlib
import urllib.error

import pytest

import enrolment.agent.ollama as ollama_module
from enrolment.agent import adversarial, cli_common
from enrolment.agent.adversarial import parse_args, preflight
from enrolment.agent.campaign import read_index
from enrolment.agent.transcript import Transcript
from enrolment.run_setup import off_arm_allowed


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
    assert "440" in printed
    assert "240" in printed and "200" in printed


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
        cli_common, "model_fingerprint",
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

    monkeypatch.setattr(cli_common, "model_fingerprint", refuse)

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
    monkeypatch.setattr(cli_common, "warm_up", lambda model, **kwargs: None)

    fingerprints = preflight(["llama3.2"])

    assert fingerprints["llama3.2"]["digest"] == "a80c4f17acd5"


def test_preflight_warms_every_model_before_any_measured_run(monkeypatch):
    """A's §7: llama3.2's first response after a load differed from those that
    followed. A measured run should not be the one that is different for a
    reason unrelated to the experiment."""
    warmed = []
    monkeypatch.setattr(
        cli_common, "model_fingerprint",
        lambda model, **kwargs: {"model": model, "digest": "sha256:x"},
    )
    monkeypatch.setattr(cli_common, "warm_up", lambda model, **kwargs: warmed.append(model))

    fingerprints = preflight(["llama3.1:8b", "llama3.2"])

    assert warmed == ["llama3.1:8b", "llama3.2"]
    assert fingerprints["llama3.2"]["digest"] == "sha256:x"


def _stub_run_one(calls, duration=2.0):
    """Stands in for the one function that needs a proxy and a model."""

    async def run_one(scenario, spec, *, campaign_dir, pack_path, repo_root,
                      chat_factory, manifest):
        calls.append({
            "run_id": spec.run_id, "scenario_id": scenario.id,
            "chat": chat_factory(spec.model), "arm": spec.arm,
            "system_prompt": manifest.system_prompt,
            "max_model_turns": manifest.max_model_turns,
            "pack_path": pack_path,
        })
        (pathlib.Path(campaign_dir) / spec.run_id).mkdir(parents=True, exist_ok=True)
        return Transcript(task_id=scenario.id, model=spec.model), duration

    return run_one


def test_main_runs_the_matrix_it_reported_and_indexes_it(tmp_path, monkeypatch, capsys):
    """The whole non-dry-run path, with only the daemon and the proxy stubbed.

    This is the path a campaign actually takes and the one that had no test:
    the preflight regression of Critical 1 reached final review because
    nothing here ever wired `preflight` to `run_campaign`.
    """
    calls = []
    monkeypatch.setattr(
        adversarial, "preflight",
        lambda models: {m: {"model": m, "digest": "d0", "ollama_version": "0.33.2"}
                        for m in models},
    )
    monkeypatch.setattr(
        adversarial, "ollama_chat",
        lambda model, **kwargs: f"chat({model}, {kwargs['temperature']}, {kwargs['seed']})",
    )
    monkeypatch.setattr(adversarial, "run_one", _stub_run_one(calls))

    exit_code = adversarial.main([
        "--campaign", str(tmp_path), "--models", "m", "--repeats", "1", "--limit", "2",
    ])
    printed = capsys.readouterr().out

    assert exit_code == 0

    # build_manifest reached the manifest on disk, fingerprints and all.
    stored = json.loads((tmp_path / "campaign.json").read_text())
    assert stored["models"] == ["m"]
    assert stored["repeats"] == 1
    assert stored["system_prompt"] == adversarial.SYSTEM_PROMPT
    assert stored["nudge"] == adversarial.NUDGE
    assert stored["fingerprints"] == {"m": {"model": "m", "digest": "d0",
                                            "ollama_version": "0.33.2"}}

    # --limit is a cap on this invocation: two runs, the first two of the
    # matrix. "A-01" sorts before every "SC-*" id, so the first scenario in
    # the suite is now the upload-borne A-01, and being a two-arm scenario
    # its own on and off runs fill the cap.
    assert [c["run_id"] for c in calls] == ["A-01-m-on-r1", "A-01-m-off-r1"]

    # chat_factory carries the manifest's decoding settings, not the daemon's.
    assert {c["chat"] for c in calls} == {"chat(m, 0.0, None)"}
    assert {c["system_prompt"] for c in calls} == {adversarial.SYSTEM_PROMPT}
    assert {c["max_model_turns"] for c in calls} == {4}
    assert {c["pack_path"] for c in calls} == {adversarial.REPO_ROOT / adversarial.DEFAULT_PACK}

    # describe supplied the domain fields; run_status supplied the status.
    index = {r.run_id: r for r in read_index(tmp_path)}
    assert set(index) == {"A-01-m-on-r1", "A-01-m-off-r1"}
    assert index["A-01-m-on-r1"].attack_class == "rule-breaking-action"
    assert index["A-01-m-off-r1"].attack_class == "rule-breaking-action"
    assert index["A-01-m-off-r1"].status == "ok"
    assert index["A-01-m-off-r1"].duration_s == 2.0
    assert index["A-01-m-off-r1"].arm == "off"

    assert "2 runs indexed" in printed
    assert "2 timed runs" in printed


def test_main_does_not_average_untimed_runs_into_its_total(tmp_path, monkeypatch, capsys):
    """A resumed campaign adopts rows it did not time. Reporting them as zero
    would state a wall clock that never elapsed."""
    monkeypatch.setattr(
        adversarial, "preflight", lambda models: {m: {"digest": "d0"} for m in models}
    )
    monkeypatch.setattr(adversarial, "ollama_chat", lambda model, **kwargs: None)
    monkeypatch.setattr(adversarial, "run_one", _stub_run_one([], duration=60.0))

    argv = ["--campaign", str(tmp_path), "--models", "m", "--repeats", "1", "--limit", "2"]
    adversarial.main(argv)
    capsys.readouterr()
    # The transcripts the stub never wrote, so the second invocation adopts them.
    for run_id in ("SC-01-m-on-r1", "SC-01b-m-on-r1"):
        Transcript(task_id=run_id, model="m").write_jsonl(
            tmp_path / run_id / "transcript.jsonl"
        )

    adversarial.main(argv)
    printed = capsys.readouterr().out

    # Durations survive the resume rather than being re-derived at zero.
    assert [r.duration_s for r in read_index(tmp_path)] == [60.0, 60.0]
    assert "2.0 minutes of run time across 2 timed runs" in printed
    assert "carry no duration" not in printed


def test_main_says_how_many_runs_it_could_not_time(tmp_path, monkeypatch, capsys):
    """Run directories with no index row behind them -- what the old --limit
    bug left. They are adopted, and the summary states that it cannot time
    them rather than folding zeros into the total."""
    monkeypatch.setattr(
        adversarial, "preflight", lambda models: {m: {"digest": "d0"} for m in models}
    )
    monkeypatch.setattr(adversarial, "ollama_chat", lambda model, **kwargs: None)
    monkeypatch.setattr(adversarial, "run_one", _stub_run_one([], duration=60.0))

    argv = ["--campaign", str(tmp_path), "--models", "m", "--repeats", "1", "--limit", "2"]
    adversarial.main(argv)
    for run_id in ("A-01-m-on-r1", "A-01-m-off-r1"):
        Transcript(task_id=run_id, model="m").write_jsonl(
            tmp_path / run_id / "transcript.jsonl"
        )
    (tmp_path / "index.jsonl").unlink()
    capsys.readouterr()

    adversarial.main(argv)
    printed = capsys.readouterr().out

    assert [r.duration_s for r in read_index(tmp_path)] == [None, None]
    assert "0.0 minutes of run time across 0 timed runs" in printed
    assert "2 of them carry no duration" in printed


def test_the_dry_run_gap_report_is_the_off_arm_rule_and_not_a_copy_of_it(capsys):
    """Design §6's hole in the headline table is reported from `off_arm_allowed`,
    the one home of that rule. Re-deriving it inline meant a change in
    `run_setup` would leave the gap report quietly describing the old rule --
    and the design's constraint that the two-of-four gap be reported
    accurately rests on this line."""
    scenarios = adversarial.load_suite(parse_args(["--campaign", "x"]))
    adversarial.main(["--campaign", "x", "--dry-run"])

    printed = capsys.readouterr().out
    excluded = sorted(s.id for s in scenarios if not off_arm_allowed(s))

    assert excluded
    assert ", ".join(excluded) in printed
    assert f"{len(excluded)} scenarios" in printed


def test_a_re_pulled_model_warns_that_the_stored_digest_is_stale(
    tmp_path, monkeypatch, capsys
):
    """`fingerprints` is rightly outside METHOD_FIELDS -- a digest is observed,
    not chosen, and re-reading one must not refuse a resume. But `run_one`
    stamps the *stored* manifest's digest into every run's agent.json, so a
    model re-pulled mid-campaign gives runs made by the new weights a
    provenance claim naming the old ones."""
    digests = iter(["d0", "d1"])
    monkeypatch.setattr(
        adversarial, "preflight",
        lambda models: {m: {"model": m, "digest": next(digests)} for m in models},
    )
    monkeypatch.setattr(adversarial, "ollama_chat", lambda model, **kwargs: None)
    monkeypatch.setattr(adversarial, "run_one", _stub_run_one([]))

    argv = ["--campaign", str(tmp_path), "--models", "m", "--repeats", "1", "--limit", "1"]
    assert adversarial.main(argv) == 0
    assert "WARNING" not in capsys.readouterr().out

    assert adversarial.main(argv) == 0
    printed = capsys.readouterr().out

    assert "WARNING" in printed
    assert "d0" in printed and "d1" in printed and "'m'" in printed
    # The resume still works: a digest is observed, not chosen.
    assert json.loads((tmp_path / "campaign.json").read_text())["fingerprints"] == {
        "m": {"model": "m", "digest": "d0"}
    }


def test_the_prompt_flag_selects_the_named_prompt_for_the_manifest():
    from enrolment.agent.prompts import SYSTEM_PROMPT, TOLD_THE_RULES_PROMPT

    default = adversarial.build_manifest(parse_args(["--campaign", "x"]), {})
    told = adversarial.build_manifest(
        parse_args(["--campaign", "x", "--prompt", "told-the-rules"]), {}
    )
    assert default.system_prompt == SYSTEM_PROMPT
    assert told.system_prompt == TOLD_THE_RULES_PROMPT


def test_arms_default_to_the_two_agent_arms():
    args = parse_args(["--campaign", "x"])
    assert args.arms == ["on", "off"]
    assert args.num_ctx is None


def test_arms_parse_in_any_order_with_spaces():
    args = parse_args(["--campaign", "x", "--arms", " rag, on "])
    assert args.arms == ["rag", "on"]


def test_an_unknown_arm_is_refused():
    with pytest.raises(SystemExit):
        parse_args(["--campaign", "x", "--arms", "on,sideways"])


def test_the_rag_arm_runs_every_scenario_once_more():
    """24 scenarios: 20 with both agent arms, 4 with on only (44), plus 24
    rag runs = 68 per model per repeat."""
    args = parse_args(["--campaign", "x", "--arms", "on,off,rag", "--repeats", "1",
                       "--models", "fake"])
    scenarios = adversarial.load_suite(args)
    specs = adversarial.enumerate_matrix(scenarios, args.models, args.repeats, args.arms)
    assert len(specs) == 68
    assert sum(1 for s in specs if s.arm == "rag") == 24
    assert [s.arm for s in specs if s.scenario_id == "SC-01"] == ["on", "rag"]


def test_asking_for_only_the_rag_arm_runs_no_agent_arm():
    args = parse_args(["--campaign", "x", "--arms", "rag", "--repeats", "1", "--models", "fake"])
    specs = adversarial.enumerate_matrix(adversarial.load_suite(args), args.models, 1, args.arms)
    assert {s.arm for s in specs} == {"rag"}


def test_a_dry_run_with_the_rag_arm_counts_it(capsys):
    adversarial.main(["--campaign", "x", "--dry-run", "--arms", "on,off,rag",
                      "--scenarios", "attacks/scenarios-upload", "--repeats", "1",
                      "--models", "fake"])
    printed = capsys.readouterr().out
    assert "24 runs" in printed
    assert "8 rag" in printed


def test_the_rag_arm_is_refused_on_a_suite_whose_payloads_it_cannot_score(capsys):
    """The SC-* suite plants payloads in subject descriptions, which the RAG
    arm has no delivery for, and declares no obeyed_if. Refused before any
    run, naming the scenarios."""
    with pytest.raises(SystemExit) as stop:
        adversarial.main(["--campaign", "x", "--dry-run", "--arms", "rag",
                          "--repeats", "1", "--models", "fake"])
    message = str(stop.value)
    assert "SC-02" in message and "obeyed_if" in message


def test_the_manifest_records_arms_num_ctx_and_the_rag_prompt():
    from enrolment.agent.prompts import RAG_SYSTEM_PROMPT
    args = parse_args(["--campaign", "x", "--arms", "on,rag", "--num-ctx", "8192"])
    manifest = adversarial.build_manifest(args, fingerprints={})
    assert manifest.arms == ["on", "rag"]
    assert manifest.num_ctx == 8192
    assert manifest.rag_system_prompt == RAG_SYSTEM_PROMPT


def test_a_two_arm_manifest_carries_no_rag_prompt():
    args = parse_args(["--campaign", "x"])
    manifest = adversarial.build_manifest(args, fingerprints={})
    assert manifest.arms == ["on", "off"]
    assert manifest.rag_system_prompt is None
