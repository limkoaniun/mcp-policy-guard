"""Run-directory setup, shared by both arms of the scenario suite.

`scenario_runner` replays hand-scripted calls; `agent.adversarial` drives a
model. Both spawn the same proxy against the same kind of run directory, and
the directory must be built the same way by each -- two arms writing
`task.yaml` differently is the silent-divergence shape this project already
had to close between the proxy's validators and the upstream server's own
`--students` argument.

The baseline-dataset lookup and the off-arm eligibility rule live here for
the same reason: it is consulted
by the replay arm (which refuses the off arm outright) and by the model arm
(which runs it for the scenarios that can mount an attack), and the two must
not be able to disagree about which those are.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from policy_guard.config import load_policy_pack

from .scenarios_spec import Scenario


def off_arm_allowed(scenario: Scenario) -> bool:
    """Whether a model-driven enforcement-off run of `scenario` measures anything.

    A benign scenario mounts no attack, so nothing can fail to be delivered and
    its off arm is the utility control. An injected scenario needs its payload
    to actually land: `deliverable: false` means no mechanism delivers it, and
    running the off arm anyway would report "the attack failed" when no attack
    was ever mounted. `scenarios_spec.UNDELIVERABLE_CLASSES` is where that flag
    is enforced at load time; this is the same rule at run time.
    """
    return not scenario.injected or scenario.deliverable


def arms_for(scenario: Scenario) -> list[str]:
    """The enforcement arms `scenario` may be run under, in matrix order."""
    return ["on", "off"] if off_arm_allowed(scenario) else ["on"]


def write_task(
    run_dir,
    *,
    task_id: str,
    description: str,
    allowed_tools: list[str],
    principal_id: str,
    overlay_path=None,
    attachments_path=None,
) -> Path:
    """The `task.yaml` the proxy is launched against.

    Takes the four fields rather than a `Scenario`, because the compliance
    suite has no `Scenario` to pass -- its scenarios are (student, subject)
    pairs. Two suites building `task.yaml` differently is exactly what this
    module was extracted to prevent, so both call this.
    """
    run_dir = Path(run_dir)
    task = {
        "task_id": task_id,
        "description": description,
        "allowed_tools": list(allowed_tools),
        "principal_id": principal_id,
    }
    if overlay_path is not None:
        task["overlay"] = str(overlay_path)
    if attachments_path is not None:
        task["attachments"] = str(attachments_path)
    path = run_dir / "task.yaml"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    return path


def write_overlay(scenario: Scenario, run_dir) -> Path | None:
    """Deliver the payload when it can be delivered.

    The replay arm has no model to read it, and writes it anyway because
    `load_overlay` validates that the target subject exists -- so a typo fails
    loudly here rather than producing a scenario whose off arm could never have
    worked. The model arm needs it delivered for real, on both arms: an
    enforcement-on run of an injected scenario is only evidence if the
    injection actually reached the model.
    """
    run_dir = Path(run_dir)
    payload = scenario.payload
    if payload is None or not scenario.deliverable:
        return None
    if payload.location == "subject_description":
        path = run_dir / "overlay.yaml"
        path.write_text(
            yaml.safe_dump({payload.target: {"description_suffix": payload.text}}),
            encoding="utf-8",
        )
        return path
    if payload.location == "attachment":
        # Delivered by `write_attachments`, not by the overlay.
        return None
    # Coupled to `scenarios_spec.DELIVERY_MECHANISMS` only by convention -- a
    # location added there without a matching branch here would otherwise fall
    # through to `return None`, running a `deliverable: true` scenario with no
    # payload ever delivered and nothing saying so.
    raise ValueError(
        f"{scenario.id}: payload location {payload.location!r} was accepted "
        f"by the loader as deliverable, but run_setup implements no delivery "
        f"mechanism for it"
    )


def plant_payload(text: str, payload_text: str) -> str:
    """The planted document: the payload appended after a blank line -- the
    position a hidden footer or a note "from the institution" would occupy in
    a real template. The demo's attach control and the campaign's
    `write_attachments` both call this, so the bytes the demo shows are the
    bytes the campaign measured."""
    return text.rstrip() + "\n\n" + payload_text.strip()


def write_attachments(scenario: Scenario, run_dir) -> Path | None:
    """Write the run's uploaded documents, delivering the payload inside one.

    The twin rule is what makes this fair: both twins carry the same
    `attachments` block, so the clean document is byte-identical across the
    pair and the injected run differs only by the appended payload. The
    planted variant is `plant_payload`'s.
    """
    if not scenario.attachments:
        return None
    documents = {
        attachment.id: {"name": attachment.name, "text": attachment.text}
        for attachment in scenario.attachments
    }
    payload = scenario.payload
    if payload is not None and scenario.deliverable and payload.location == "attachment":
        target = documents[payload.target]
        target["text"] = plant_payload(target["text"], payload.text)
    run_dir = Path(run_dir)
    path = run_dir / "attachments.json"
    path.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def students_baseline_path(pack_path, repo_root) -> Path:
    """The dataset a scenario's "enrolment unchanged" baseline is compared
    against.

    Named by the pack's own `domain_rules_args["students_path"]`, never
    hardcoded: a pack other than the personas one would otherwise be silently
    compared against the wrong file -- the same fail-open, silent-divergence
    shape `students_path` was itself added to close between the proxy's
    validators and the upstream server's mutable run state. There is no
    fallback for a pack that omits it: the same field drives the upstream
    server's own `--students` argument, so guessing a default here could only
    ever be right by coincidence.
    """
    pack = load_policy_pack(Path(pack_path))
    students_path = pack.domain_rules_args.get("students_path")
    if students_path is None:
        raise ValueError(
            f"policy pack {pack.name!r} ({pack_path}) names no "
            f"domain_rules_args['students_path'], so there is no baseline "
            f"dataset to compare a scenario's final enrolment against"
        )
    path = Path(students_path)
    if not path.is_absolute():
        path = Path(repo_root) / path
    return path
