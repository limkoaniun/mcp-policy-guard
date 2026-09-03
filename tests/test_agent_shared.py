"""The pieces both suites share, exercised from their new homes.

Each of these moved because subsystem C's arrival would otherwise have given
one rule two homes -- the divergence shape this codebase has already closed
twice.
"""

import pytest

from enrolment.agent import cli_common
from enrolment.agent.campaign import RunSpec, model_slug
from enrolment.agent.prompts import NUDGE, SYSTEM_PROMPT
from enrolment.run_setup import write_task


def test_the_nudge_has_one_home():
    """Both suites import this. If they carried separate copies their attack
    success and violation rates would not be comparable, and nothing would say
    so."""
    from enrolment.agent import adversarial

    assert adversarial.NUDGE is NUDGE
    assert adversarial.SYSTEM_PROMPT is SYSTEM_PROMPT


def test_the_system_prompt_still_takes_a_principal():
    assert "{principal}" in SYSTEM_PROMPT


def test_run_spec_moved_without_changing_its_run_id():
    spec = RunSpec(scenario_id="SC-07", model="llama3.1:8b", arm="on", repeat=3)
    assert spec.run_id == "SC-07-llama318b-on-r3"
    assert spec.replacement().run_id == "SC-07-llama318b-on-r3a"


def test_model_slug_moved_unchanged():
    assert model_slug("llama3.1:8b") == "llama318b"
    assert model_slug("llama3.2") == "llama32"


def test_campaign_still_imports_nothing_from_the_domain():
    """RunSpec moving in must not drag a domain import with it. `campaign.py`
    is reused unchanged by subsystem C precisely because it has none."""
    from enrolment.agent import campaign

    source = campaign.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for forbidden in (
        "from ..scenarios", "from ..scenarios_spec", "from ..run_setup",
        "from .adversarial", "from .compliance", "import enrolment",
    ):
        assert forbidden not in text, f"campaign.py imports {forbidden!r}"


def test_write_task_takes_keyword_arguments(tmp_path):
    """Both suites now build task.yaml through this one function; a compliance
    scenario has no `Scenario` object to pass."""
    import yaml

    path = write_task(
        tmp_path,
        task_id="S1000001-32933",
        description="I'd like to enrol in 32933 Research Project.",
        allowed_tools=["check_prerequisites", "enrol"],
        principal_id="S1000001",
    )
    written = yaml.safe_load(path.read_text())
    assert written["task_id"] == "S1000001-32933"
    assert written["principal_id"] == "S1000001"
    assert written["allowed_tools"] == ["check_prerequisites", "enrol"]
    assert "overlay" not in written


def test_write_task_records_an_overlay_when_given_one(tmp_path):
    import yaml

    path = write_task(
        tmp_path, task_id="SC-07", description="d", allowed_tools=[],
        principal_id="S9000002", overlay_path=tmp_path / "overlay.yaml",
    )
    assert "overlay" in yaml.safe_load(path.read_text())


def test_preflight_moved_and_still_names_the_missing_model():
    with pytest.raises(RuntimeError) as error:
        cli_common.preflight(
            ["llama3.2"],
            fingerprint=lambda m, **k: {"model": m, "digest": "unknown"},
            warm=lambda m, **k: None,
        )
    assert "llama3.2" in str(error.value)
    assert "ollama pull" in str(error.value)


def test_preflight_warms_every_model():
    warmed = []
    fingerprints = cli_common.preflight(
        ["a", "b"],
        fingerprint=lambda m, **k: {"model": m, "digest": "sha256:x"},
        warm=lambda m, **k: warmed.append(m),
    )
    assert warmed == ["a", "b"]
    assert set(fingerprints) == {"a", "b"}


def test_digest_drift_warns_naming_both_digests(capsys):
    cli_common.warn_on_digest_drift({"m": {"digest": "old"}}, {"m": {"digest": "new"}})
    printed = capsys.readouterr().out
    assert "old" in printed and "new" in printed and "m" in printed


def test_no_drift_warning_when_the_digest_is_unchanged(capsys):
    cli_common.warn_on_digest_drift({"m": {"digest": "same"}}, {"m": {"digest": "same"}})
    assert capsys.readouterr().out == ""
