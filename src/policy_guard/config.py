"""Task definitions and policy packs.

This is the seam that keeps `policy_guard` domain-free. The proxy learns which
tools exist, which are trustworthy, and how to start the upstream server from a
pack file — never by importing the domain. Swapping `configs/enrolment.yaml`
for another pack is what makes the proxy domain-agnostic in fact rather than in
claim.

The allowlist comes from the task definition and never from the agent. An agent
able to declare its own allowlist could widen it under injection, and the
defence would be circular.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from .models import TaskContext

TRUST_LABELS = {"trusted", "untrusted"}


class TaskDefinition(BaseModel):
    task_id: str
    description: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    principal_id: str | None = None
    overlay: str | None = None


class PolicyPack(BaseModel):
    name: str
    upstream_command: str
    upstream_args: list[str] = Field(default_factory=list)
    # `validate_default=True` so the validator below also fires when the key is
    # omitted entirely -- an omitted `policies:` is the same allow-everything
    # engine as an empty one, and must fail the same way.
    policies: list[str] = Field(default_factory=list, validate_default=True)
    trust: dict[str, str] = Field(default_factory=dict)
    principal_argument: str | None = None
    # Tools that legitimately take no principal argument (e.g. a catalogue
    # search with no student to bind to), and so must not be denied merely
    # for omitting it. Named as the EXEMPT set, not the scoped one: a tool
    # left off this list stays enforced by default, where a scoped-list
    # would silently drop binding for anything an author forgot to add.
    principal_exempt_tools: list[str] = Field(default_factory=list)
    # Import path to the domain's validator factory, "module:callable".
    # The generic layer resolves it without knowing what it builds.
    domain_rules: str | None = None
    # Keyword arguments passed to the resolved `domain_rules` factory,
    # alongside the run directory. This is what lets the pack be the single
    # source of truth for where a domain's own data lives, instead of that
    # configuration being duplicated into environment variables that nothing
    # keeps in sync with the upstream server's own arguments.
    domain_rules_args: dict[str, str] = Field(default_factory=dict)

    @field_validator("policies")
    @classmethod
    def _at_least_one_policy(cls, value: list[str]) -> list[str]:
        """A pack naming no policies is rejected rather than defaulted.

        `PolicyEngine([])` returns ALLOW / "no policy objected" for every call,
        so a pack with an empty (or omitted) `policies:` list would invert
        deny-by-default into allow-everything -- and the run would be
        indistinguishable, in `decisions.jsonl`, from a genuine enforcement-on
        run in which no rule happened to object. That is a failure presenting
        as a confident success, biased toward the defence, so it fails loudly
        at load time instead.
        """
        if not value:
            raise ValueError(
                "policy pack names no policies; an empty `policies:` list would "
                "make the engine allow every call, which is indistinguishable "
                "from enforcement that had nothing to object to"
            )
        return value

    @field_validator("trust")
    @classmethod
    def _known_labels(cls, value: dict[str, str]) -> dict[str, str]:
        for tool, label in value.items():
            if label not in TRUST_LABELS:
                raise ValueError(
                    f"unknown trust label {label!r} for tool {tool!r}; "
                    f"expected one of {sorted(TRUST_LABELS)}"
                )
        return value


def _read(path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_task(path) -> TaskDefinition:
    return TaskDefinition.model_validate(_read(path))


def load_policy_pack(path) -> PolicyPack:
    return PolicyPack.model_validate(_read(path))


def build_context(task: TaskDefinition) -> TaskContext:
    return TaskContext(
        task_id=task.task_id,
        description=task.description,
        allowed_tools=frozenset(task.allowed_tools),
        principal_id=task.principal_id,
    )
