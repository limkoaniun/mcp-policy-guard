"""Policy module: argument validation.

Two responsibilities, both generic:

- **Principal binding.** A task is scoped to one principal. A call naming a
  different one is denied outright, whatever the agent's reason. The pack's
  `principal_argument` names which tool argument carries it, so no domain
  noun enters this package. Not every tool takes that argument -- a
  catalogue search has no student to bind to -- so the pack also declares
  `exempt_tools`: tools that legitimately omit the principal argument, and so
  are not denied merely for lacking it. Deny-on-*mismatch* stays unconditional
  for every tool, exempt or not -- exemption means "may omit the argument",
  never "may name a different principal". The pack names the exempt tools
  rather than the scoped ones on purpose: forgetting to list a tool leaves it
  enforced (fail-closed), where a scoped-list would silently disable binding
  for anything omitted from it.
- **Domain rules.** The pack supplies validators keyed by tool name. A
  validator returns a deny reason, or `None` if it has no objection. All the
  domain knowledge lives on the far side of that callable.

A validator that raises is not swallowed: `enforcement.py` synthesises a DENY
around a raising policy, which is the correct outcome. The layer must never
permit an action it could not verify.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from ..models import Decision, TaskContext, ToolCall, Verdict

Validator = Callable[[dict], str | None]


class ArgumentValidationPolicy:
    name = "arg_validation"

    def __init__(
        self,
        principal_argument: str | None = None,
        validators: Mapping[str, Validator] | None = None,
        exempt_tools: Iterable[str] | None = None,
    ):
        self.principal_argument = principal_argument
        self.validators = dict(validators or {})
        self.exempt_tools = frozenset(exempt_tools or ())

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
            if call.tool in self.exempt_tools:
                # Declared by the pack as legitimately not principal-scoped
                # (e.g. a catalogue search that takes no student argument).
                # Only the absent-argument branch is exempt -- a call naming
                # the *wrong* principal falls through to the unconditional
                # deny below regardless of exemption.
                return None
            return Decision(
                verdict=Verdict.DENY,
                policy=self.name,
                code="principal_missing",
                reason=(
                    f"'{call.tool}' was called without '{self.principal_argument}', "
                    f"so it could not be confirmed to act on {context.principal_id}. "
                    f"An action that cannot be verified is not permitted."
                ),
            )
        return Decision(
            verdict=Verdict.DENY,
            policy=self.name,
            code="principal_mismatch",
            reason=(
                f"this task may only act on {context.principal_id}, but "
                f"'{call.tool}' was called for {named}."
            ),
        )
