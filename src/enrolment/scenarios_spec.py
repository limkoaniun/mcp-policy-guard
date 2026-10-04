"""Scenario definitions for the persona suite.

`attacks/README.md` fixes the four attack classes and the judging rule; this
module is the schema that makes a scenario file executable.

The validation here exists for one reason. A scenario declaring a payload no
mechanism can deliver would load cleanly, pass its replay arm, and count
toward a coverage claim it does not support -- and the enforcement-off control
would read "the attack didn't work" when in truth no attack was ever mounted.
`load_overlay` in `server.py` already refuses that shape of mistake for
overlays; this refuses it for scenarios.

Unrelated to `enrolment.scenarios`, which generates the compliance suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

ATTACK_CLASSES = {
    # Technique classes of the original suite (payload in a subject
    # description; two undeliverable). Retained; see attacks/README.md.
    "output-injection",
    "poisoned-description",
    "tool-shadowing",
    "exfiltration",
    # Upload-borne classes (2026-09-23), named for what the planted text asks
    # the agent to do, because that is what the ablation attributes.
    "rule-breaking-action",
    "out-of-task-action",
    "cross-principal-action",
}

# Payload delivery mechanisms that actually exist. `subject_description` maps
# to the overlay's `description_suffix`; `attachment` appends the payload to a
# named uploaded document (`run_setup.write_attachments`). Nothing yet patches
# tool metadata or registers a decoy tool.
DELIVERY_MECHANISMS = {"subject_description", "attachment"}

# Attack classes with no delivery mechanism implemented yet. A scenario in
# one of these classes must be `deliverable: false` regardless of whether it
# carries a payload at all -- `tool-shadowing` (like `exfiltration`) is
# exempt from needing a payload, since its attack is the call itself, and
# that exemption left `deliverable` validated by nothing for it: the
# payload-location check below never runs when there is no payload to check.
#
# Removing `poisoned-description` from this set requires an overlay
# mechanism that patches a tool's own declared metadata, not just a
# catalogue entry's description. Removing `tool-shadowing` requires
# registering a decoy tool alongside the real ones.
UNDELIVERABLE_CLASSES = {"poisoned-description", "tool-shadowing"}


class ScenarioError(ValueError):
    """A scenario file that could not be run as written."""


class Expectation(BaseModel):
    verdict: Literal["allow", "deny", "require_approval"]
    policy: str | None = None
    reason_contains: str | None = None


class ReplayCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    expect: Expectation


class Payload(BaseModel):
    location: str
    target: str
    text: str
    #: What an obeyed attack looks like in reply *text*: every phrase here,
    #: case-insensitively, as a substring of the reply. Read only by the RAG
    #: arm's judge, where no state can change and the question becomes
    #: "did the reply echo the instruction?". All-of rather than any-of, so
    #: one common word cannot fire alone. A heuristic, labelled as one
    #: wherever it is reported (metrics design, RAG arm spec §2).
    obeyed_if: list[str] = Field(default_factory=list)


class AttachmentSpec(BaseModel):
    """A document the persona uploads in this scenario, in the clean form.

    An injected scenario's payload with `location: attachment` is appended to
    the document named by `payload.target` at run time; the benign twin
    carries the identical block and no payload, so the two runs differ only
    in the planted text.
    """

    id: str
    name: str
    text: str


class TaskSpec(BaseModel):
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list)


class FinalState(BaseModel):
    enrolled_unchanged: bool = True
    erequests: int = 0


class Scenario(BaseModel):
    id: str
    persona: str
    erequest_action: str
    attack_class: str | None = None
    deliverable: bool = True
    task: TaskSpec
    payload: Payload | None = None
    attachments: list[AttachmentSpec] = Field(default_factory=list)
    replay: list[ReplayCall] = Field(default_factory=list)
    final_state: FinalState = Field(default_factory=FinalState)

    @property
    def injected(self) -> bool:
        return self.attack_class is not None


def load_scenario(path, *, personas, known_tools) -> Scenario:
    """Parse and validate one scenario file.

    `personas` is the set of student ids the suite may act on; `known_tools`
    the tools the upstream actually exposes.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as error:
        raise ScenarioError(f"{path}: {error}") from error

    if scenario.attack_class is not None and scenario.attack_class not in ATTACK_CLASSES:
        raise ScenarioError(
            f"{path}: unknown attack_class {scenario.attack_class!r}; "
            f"expected one of {sorted(ATTACK_CLASSES)}"
        )

    if scenario.attack_class in UNDELIVERABLE_CLASSES and scenario.deliverable:
        raise ScenarioError(
            f"{path}: attack_class {scenario.attack_class!r} has no delivery "
            f"mechanism implemented yet (see UNDELIVERABLE_CLASSES), so no "
            f"scenario in this class may claim deliverable: true -- its "
            f"enforcement-off arm would report 'the attack failed' when no "
            f"attack was ever mounted"
        )

    if not scenario.replay:
        raise ScenarioError(
            f"{path}: replay is empty; a scenario that replays no calls "
            f"asserts nothing about enforcement"
        )

    if scenario.injected and scenario.payload is None and scenario.attack_class not in {
        "tool-shadowing",
        "exfiltration",
    }:
        # Shadowing and exfiltration need no injected text: the attack is the
        # call itself. Every other class must say what would be injected, or
        # the scenario is not a description of an attack at all.
        raise ScenarioError(
            f"{path}: attack_class {scenario.attack_class!r} requires a payload"
        )

    if not scenario.injected and scenario.payload is not None:
        raise ScenarioError(
            f"{path}: a benign scenario carries a payload; benign and injected "
            f"variants differ only in whether a payload is present"
        )

    if scenario.payload is not None:
        location = scenario.payload.location
        if location not in DELIVERY_MECHANISMS and scenario.deliverable:
            raise ScenarioError(
                f"{path}: payload location {location!r} is not implemented "
                f"(have {sorted(DELIVERY_MECHANISMS)}), but the scenario claims "
                f"deliverable: true. A payload that cannot land must be declared "
                f"deliverable: false, or its enforcement-off arm would report "
                f"'the attack failed' when no attack was ever mounted."
            )

    ids = [attachment.id for attachment in scenario.attachments]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ScenarioError(
            f"{path}: attachment ids {duplicates} are declared more than once"
        )
    if scenario.payload is not None and scenario.payload.location == "attachment":
        if scenario.payload.target not in ids:
            raise ScenarioError(
                f"{path}: payload targets attachment {scenario.payload.target!r}, "
                f"which the scenario does not declare (have {ids}). A payload "
                f"that cannot land must not load as one that did."
            )

    if scenario.persona not in personas:
        raise ScenarioError(
            f"{path}: persona {scenario.persona!r} is not in the persona set "
            f"{sorted(personas)}"
        )

    for call in scenario.replay:
        if call.tool not in known_tools and scenario.attack_class != "tool-shadowing":
            raise ScenarioError(
                f"{path}: replay names tool {call.tool!r}, which the upstream does "
                f"not expose. Only a tool-shadowing scenario may name a tool that "
                f"does not exist."
            )

    return scenario


def load_all(root, *, personas, known_tools) -> list[Scenario]:
    """Every scenario under `root`, sorted by id."""
    scenarios = [
        load_scenario(path, personas=personas, known_tools=known_tools)
        for path in sorted(Path(root).rglob("*.yaml"))
    ]
    return sorted(scenarios, key=lambda s: s.id)
