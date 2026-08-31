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
    "output-injection",
    "poisoned-description",
    "tool-shadowing",
    "exfiltration",
}

# Payload delivery mechanisms that actually exist. `subject_description` maps
# to the overlay's `description_suffix`; nothing yet patches tool metadata or
# registers a decoy tool.
DELIVERY_MECHANISMS = {"subject_description"}


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
