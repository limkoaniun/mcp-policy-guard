"""Policy module: tool allowlist.

Each task declares the set of tools it may use (`TaskContext.allowed_tools`).
Any call to a tool outside that set is denied. Deny-by-default: an empty
allowlist denies every tool call.

This is the first line of defence against prompt injection: a hijacked agent
cannot invoke a destructive tool (e.g. `drop`) during a task that only
declared read-only tools, no matter what the injected text says.
"""

from ..models import Decision, TaskContext, ToolCall, Verdict


class AllowlistPolicy:
    name = "allowlist"

    def check(self, call: ToolCall, context: TaskContext) -> Decision:
        if call.tool in context.allowed_tools:
            return Decision(
                verdict=Verdict.ALLOW,
                policy=self.name,
                reason=f"'{call.tool}' is declared for task '{context.task_id}'",
            )
        return Decision(
            verdict=Verdict.DENY,
            policy=self.name,
            reason=(
                f"'{call.tool}' is not on the allowlist for task '{context.task_id}' "
                f"(allowed: {sorted(context.allowed_tools) or 'none'})"
            ),
        )
