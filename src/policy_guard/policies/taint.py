"""Policy module: taint tracking.

The pack labels each tool's *results* as trusted or untrusted. Once a run has
read an untrusted result -- `EnforcementExtension.observe_forwarded` records
it on the context -- every later side-effecting call is *tainted*: it may
have been proposed because of text an outside party wrote. A tainted action
is not denied and not allowed; it is held for the principal's confirmation,
the engine's third verdict.

This policy reads provenance and nothing else. It does not inspect the call's
arguments and it never sees the untrusted text. Deciding what a document
"really" asked for would be a judgement, and the layer exists to keep
judgement out of the loop: the person who uploaded the document can tell a
request they made from one they did not, and the model cannot.

Which tools count as side-effecting is the pack's `side_effecting_tools`
list. Named as the acting set rather than the exempt set on purpose: a tool
left off it is treated as a read, so forgetting one is fail-open -- which is
why `config.PolicyPack` refuses a pack that loads `taint` with an empty list.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import Decision, TaskContext, ToolCall, Verdict


class TaintPolicy:
    name = "taint"

    def __init__(self, side_effecting_tools: Iterable[str]):
        self.side_effecting_tools = frozenset(side_effecting_tools)

    def check(self, call: ToolCall, context: TaskContext) -> Decision | None:
        if not context.tainted or call.tool not in self.side_effecting_tools:
            return None
        sources = ", ".join(context.taint_sources) or "an untrusted tool"
        return Decision(
            verdict=Verdict.REQUIRE_APPROVAL,
            policy=self.name,
            reason=(
                f"'{call.tool}' was proposed after the assistant read untrusted "
                f"content ({sources}), so it needs the user's confirmation before "
                f"it runs. Tell the user what you intend to do and why, and ask "
                f"them to confirm."
            ),
        )
