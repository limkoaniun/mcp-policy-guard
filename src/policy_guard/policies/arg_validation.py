"""Policy module: argument validation.

Two responsibilities, both generic:

- **Principal binding.** A task is scoped to one principal. A call naming a
  different one is denied outright, whatever the agent's reason. The pack's
  `principal_argument` names which tool argument carries it, so no domain
  noun enters this package.
- **Domain rules.** The pack supplies validators keyed by tool name. A
  validator returns a deny reason, or `None` if it has no objection. All the
  domain knowledge lives on the far side of that callable.

A validator that raises is not swallowed: `enforcement.py` synthesises a DENY
around a raising policy, which is the correct outcome. The layer must never
permit an action it could not verify.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..models import Decision, TaskContext, ToolCall, Verdict

Validator = Callable[[dict], str | None]


class ArgumentValidationPolicy:
    name = "arg_validation"

    def __init__(
        self,
        principal_argument: str | None = None,
        validators: Mapping[str, Validator] | None = None,
    ):
        self.principal_argument = principal_argument
        self.validators = dict(validators or {})

    def check(self, call: ToolCall, context: TaskContext) -> Decision | None:
        denial = self._check_principal(call, context)
        if denial is not None:
            return denial

        validator = self.validators.get(call.tool)
        if validator is None:
            return None
        reason = validator(call.arguments)
        if reason is None:
            return None
        return Decision(verdict=Verdict.DENY, policy=self.name, reason=reason)

    def _check_principal(self, call: ToolCall, context: TaskContext) -> Decision | None:
        if not self.principal_argument or not context.principal_id:
            return None
        named = call.arguments.get(self.principal_argument)
        if named == context.principal_id:
            return None
        if named is None:
            return Decision(
                verdict=Verdict.DENY,
                policy=self.name,
                reason=(
                    f"'{call.tool}' was called without '{self.principal_argument}', "
                    f"so it could not be confirmed to act on {context.principal_id}. "
                    f"An action that cannot be verified is not permitted."
                ),
            )
        return Decision(
            verdict=Verdict.DENY,
            policy=self.name,
            reason=(
                f"this task may only act on {context.principal_id}, but "
                f"'{call.tool}' was called for {named}."
            ),
        )
