"""Core data models shared by the proxy, the policy engine and the policy modules."""

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ToolCall(BaseModel):
    """A proposed tool invocation, as seen by the proxy before forwarding."""

    tool: str
    arguments: dict = Field(default_factory=dict)


class TaskContext(BaseModel):
    """What the policy engine knows about the current task and session.

    This will grow as policies are added: taint labels for taint tracking,
    per-session counters for rate/scope limits, the student scope for
    argument validation, etc.
    """

    task_id: str
    description: str = ""
    allowed_tools: frozenset[str] = frozenset()


class Decision(BaseModel):
    """The engine's ruling on a single tool call. Structured for audit logging."""

    verdict: Verdict
    policy: str
    reason: str

    def __str__(self) -> str:
        return f"[{self.verdict.value.upper():>16}] {self.policy}: {self.reason}"
