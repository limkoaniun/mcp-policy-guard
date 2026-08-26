"""Deterministic evaluation of UTS enrolment rules against a student record.

`enrolment.rules` handles the boolean expression; this module resolves each
label in that expression against what the student has actually completed,
what they are admitted to, and which area of study they are in.

Two deliberate choices, both for research validity:

- An item whose payload could not be parsed, or a label with no row in the
  published table, never counts as satisfied. It is recorded in
  `RequisiteResult.unresolved` so the evaluation harness can exclude those
  cases rather than silently scoring them as violations. This applies to
  both the requisite rule and the anti-requisite rule: an anti-requisite
  item that cannot be interpreted must not silently resolve to "not held",
  which would let a student who should be blocked pass through undetected.
  Anti-requisite labels appear in `unresolved` prefixed with
  `"antirequisite:"` so they cannot be mistaken for requisite labels that
  happen to share the same number.
- Anti-requisite rules are read literally: the student is blocked only if the
  published expression evaluates true. For `(1 AND 2)` that means holding
  both listed subjects. See the open questions in the design spec.
"""

from __future__ import annotations

import re

from .models import (
    ItemOutcome,
    RequisiteItem,
    RequisiteResult,
    Student,
    Subject,
    SubjectRequisites,
)
from .rules import Node, evaluate, labels, minimal_unmet, parse


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _is_unresolvable(item: RequisiteItem) -> bool:
    """Whether this item's payload could not be interpreted.

    Kind "unparsed" is the obvious case, but a row can also claim a kind
    while missing the one field that kind depends on (e.g. a credit-point
    item with no threshold, or an admission item with neither a course code
    nor a descriptor) if upstream parsing degraded partially. Treating those
    as resolvable would let `resolve_item` fall back to a value that happens
    to look satisfied (0 required credit points, `None == None`, an empty
    descriptor matching a blank `admitted_to` entry) instead of surfacing the
    gap. This is the single place that decides interpretability; `resolve_item`
    defers to it before running any kind-specific logic, so the two can never
    disagree.

    An unrecognised kind is unresolvable by the same argument. `resolve_item`
    already answers "could not be interpreted" for one, and if this function
    disagreed, a future `ItemKind` would be scored as a genuine violation and
    never appear in `RequisiteResult.unresolved` -- an unreliable result
    presenting as a confident one, which is exactly what this gate exists to
    prevent.
    """
    if item.kind == "unparsed":
        return True
    if item.kind == "subject":
        return not (item.subject or "").strip()
    if item.kind == "credit_points":
        return item.credit_points is None
    if item.kind == "aos":
        return not (item.aos_code or "").strip()
    if item.kind == "admission":
        return not (item.course or "").strip() and not (item.admission_descriptor or "").strip()
    return True


def _unresolved_labels(node: Node, items: dict[str, RequisiteItem]) -> list[str]:
    return sorted(
        label for label in labels(node) if label not in items or _is_unresolvable(items[label])
    )


def resolve_item(item: RequisiteItem, student: Student) -> ItemOutcome:
    """Decide whether one requisite item is satisfied by this student."""
    if _is_unresolvable(item):
        return ItemOutcome(
            label=item.label,
            satisfied=False,
            reason=f"requisite could not be interpreted: {item.text}",
        )

    if item.kind == "subject":
        if item.corequisite:
            # A corequisite may be taken alongside the subject rather than before
            # it, so current enrolment satisfies it just as prior completion does.
            held = student.completed_codes() | set(student.enrolled)
            satisfied = item.subject in held
            state = (
                "completed or concurrently enrolled"
                if satisfied
                else "neither completed nor enrolled"
            )
            return ItemOutcome(
                label=item.label,
                satisfied=satisfied,
                reason=f"{item.subject} {state}",
            )
        satisfied = item.subject in student.completed_codes()
        return ItemOutcome(
            label=item.label,
            satisfied=satisfied,
            reason=f"{item.subject} {'completed' if satisfied else 'not completed'}",
        )

    if item.kind == "credit_points":
        earned = student.completed_credit_points()
        required = item.credit_points
        return ItemOutcome(
            label=item.label,
            satisfied=earned >= required,
            reason=f"{earned} of {required} required credit points completed",
        )

    if item.kind == "aos":
        held = {code for code in (student.major, student.sub_major) if code is not None}
        satisfied = item.aos_code in held
        return ItemOutcome(
            label=item.label,
            satisfied=satisfied,
            reason=f"{item.aos_code} {'is' if satisfied else 'is not'} the student's area of study",
        )

    if item.kind == "admission":
        admitted = {_normalise(entry) for entry in student.admitted_to}
        course = _normalise(item.course or "")
        descriptor = _normalise(item.admission_descriptor or "")
        course_held = bool(course) and course in admitted
        descriptor_held = bool(descriptor) and descriptor in admitted
        satisfied = course_held or descriptor_held
        return ItemOutcome(
            label=item.label,
            satisfied=satisfied,
            reason=f"admission to '{item.admission_descriptor}' "
            f"{'held' if satisfied else 'not held'}",
        )

    return ItemOutcome(
        label=item.label,
        satisfied=False,
        reason=f"requisite could not be interpreted: {item.text}",
    )


MAX_DESCRIPTION = 200


def _describe(label: str, items: dict[str, RequisiteItem]) -> str:
    """Render one requisite for the agent-facing message, bounded in length.

    `text` is published wording and is normally a single short line, but a
    parser that mis-locates the table's end boundary can produce a very long
    one. Truncating keeps a deny message readable instead of dumping page
    furniture at the agent.
    """
    if label not in items:
        return f"requisite {label}"
    text = items[label].text
    if len(text) <= MAX_DESCRIPTION:
        return text
    return text[:MAX_DESCRIPTION].rstrip() + " [truncated]"


def _caveat(unresolved: list[str]) -> str:
    """The sentence appended whenever some requisite could not be interpreted.

    An `explanation` is the agent-facing message. When the determination rests
    on items the parser could not read, it must not be worded as though it
    were fully verified -- in either direction.
    """
    return (
        f" This determination could not be fully verified: "
        f"{len(unresolved)} requisite(s) could not be interpreted "
        f"({', '.join(unresolved)})."
    )


def _explain(
    subject_code: str,
    unmet: list[str],
    items: dict[str, RequisiteItem],
    unresolved: list[str] | None = None,
) -> str:
    unresolved = unresolved or []
    if not unmet:
        if unresolved:
            return f"No unmet requisite was found for {subject_code}." + _caveat(unresolved)
        return f"All requisites for {subject_code} are met."
    described = [_describe(label, items) for label in unmet]
    explanation = f"{subject_code} requires: " + "; ".join(described) + "."
    return explanation + _caveat(unresolved) if unresolved else explanation


def evaluate_requisites(
    subject_code: str,
    student: Student,
    requisites: dict[str, SubjectRequisites],
) -> RequisiteResult:
    record = requisites.get(subject_code)
    if record is None:
        # No record at all is not the same as a record that publishes no
        # rules: the subject was never fetched, so say so rather than assert
        # something about requisites that was never checked.
        return RequisiteResult(
            subject_code=subject_code,
            satisfied=True,
            explanation=(
                f"No requisite data was collected for {subject_code}, "
                f"so no requisites could be checked."
            ),
        )

    anti_unresolved: list[str] = []
    if record.antirequisite_rule:
        anti_node = parse(record.antirequisite_rule)
        anti_outcomes = {
            label: resolve_item(item, student) for label, item in record.antirequisite_items.items()
        }
        anti_truth = {label: outcome.satisfied for label, outcome in anti_outcomes.items()}
        anti_unresolved = [
            f"antirequisite:{label}"
            for label in _unresolved_labels(anti_node, record.antirequisite_items)
        ]
        if evaluate(anti_node, anti_truth):
            held = [
                record.antirequisite_items[label].text for label, ok in anti_truth.items() if ok
            ]
            return RequisiteResult(
                subject_code=subject_code,
                satisfied=False,
                rule=record.antirequisite_rule,
                items=anti_outcomes,
                unresolved=anti_unresolved,
                blocked_by_antirequisite=True,
                explanation=(
                    f"{subject_code} is blocked by an anti-requisite: the student has "
                    f"already completed {', '.join(held)}."
                )
                + (_caveat(anti_unresolved) if anti_unresolved else ""),
            )

    if not record.requisite_rule:
        return RequisiteResult(
            subject_code=subject_code,
            satisfied=True,
            unresolved=anti_unresolved,
            explanation=f"No requisites are published for {subject_code}."
            + (_caveat(anti_unresolved) if anti_unresolved else ""),
        )

    node = parse(record.requisite_rule)
    outcomes = {
        label: resolve_item(item, student) for label, item in record.requisite_items.items()
    }
    truth = {label: outcome.satisfied for label, outcome in outcomes.items()}

    unresolved = sorted(_unresolved_labels(node, record.requisite_items) + anti_unresolved)

    satisfied = evaluate(node, truth)
    unmet = sorted(minimal_unmet(node, truth)) if not satisfied else []

    return RequisiteResult(
        subject_code=subject_code,
        satisfied=satisfied,
        rule=record.requisite_rule,
        items=outcomes,
        unmet=unmet,
        unresolved=unresolved,
        explanation=_explain(subject_code, unmet, record.requisite_items, unresolved),
    )


def exceeds_credit_cap(
    student: Student, subject: Subject, enrolled_points: int | None = None
) -> bool:
    """Whether adding `subject` would push the student past their session cap.

    `enrolled_points` is the credit points already taken this session. Callers
    that track a session load pass it explicitly; it defaults to zero.
    """
    current = enrolled_points or 0
    return current + subject.credit_points > student.credit_point_cap_per_session
