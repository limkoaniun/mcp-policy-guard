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
    policies: list[str] = Field(default_factory=list)
    trust: dict[str, str] = Field(default_factory=dict)
    principal_argument: str | None = None

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
    )
