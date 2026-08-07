"""Policy engine: evaluates a proposed tool call against the active policy set.

Each policy module exposes `check(call, context) -> Decision`.
The engine short-circuits on the first DENY; REQUIRE_APPROVAL is sticky.
"""

from enum import Enum


class Verdict(Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class PolicyEngine:
    """Combines individual policies into a single decision. Not yet implemented."""

    def __init__(self, policies):
        self.policies = policies

    def evaluate(self, call, context) -> Verdict:
        raise NotImplementedError
