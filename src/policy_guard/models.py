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
    # Declared rather than left to `getattr(..., "tainted", False)` at the call
    # site: an absent field read defensively is a hard-coded constant wearing
    # the clothes of a measurement. Every decision record carries this value,
    # so a harness computing a tainted-call rate would have read 0% forever
    # without anything raising. Set by `EnforcementExtension.observe_forwarded`
    # when a forwarded call went to a tool the pack labels untrusted.
    tainted: bool = False
    # Which untrusted tools were read this run, in first-read order. Named in
    # the taint policy's reason so the agent can tell the user what it read.
    taint_sources: list[str] = Field(default_factory=list)
    # The one principal this task is scoped to. A call naming a different one
    # is denied by argument validation: this is what stops an injected
    # instruction operating on someone else's record.
    principal_id: str | None = None


class Decision(BaseModel):
    """The engine's ruling on a single tool call. Structured for audit logging."""

    verdict: Verdict
    policy: str
    reason: str

    def __str__(self) -> str:
        return f"[{self.verdict.value.upper():>16}] {self.policy}: {self.reason}"
