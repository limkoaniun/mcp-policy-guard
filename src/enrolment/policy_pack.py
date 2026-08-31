"""The enrolment domain's contribution to argument validation.

`src/policy_guard/` never imports this module. The pack names it by import
path and the proxy's entry point resolves it, which is what makes
"domain-agnostic" a property of the file layout rather than a claim.

Every validator returns a deny reason written for the agent to read -- it
becomes the sentence the student sees -- or `None` to raise no objection.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import Student, Subject, SubjectRequisites
from .requisites import evaluate_requisites, exceeds_credit_cap


def _load(path, model):
    return {k: model(**v) for k, v in json.loads(Path(path).read_text(encoding="utf-8")).items()}


def build_validators(data_dir, students_path, run_dir=None) -> dict:
    """Validators keyed by tool name, closed over the committed dataset.

    `catalogue` and `requisites` are the published, immutable dataset and are
    read once, here, at construction. Student enrolment is not immutable: the
    upstream server's `enrolment.state.RunState` mutates it on every `enrol`/
    `drop` call and persists the result to `<run_dir>/state.json`. A student
    dict read once from `students_path` and closed over would freeze at
    whatever the student held when the proxy started -- so the credit-point
    cap would be checked against stale state on every call after the first,
    and a student could be enrolled arbitrarily far past their cap because
    the closure never learned about its own earlier approvals. Each call to
    `enrol` below therefore re-reads `<run_dir>/state.json` when `run_dir` is
    given, so the cap binds against what the student actually holds right
    now. `students_path` remains the fallback for callers with no run
    directory (unit tests, and the case where the upstream has not yet
    written a state file).
    """
    data_dir = Path(data_dir)
    catalogue = _load(data_dir / "catalogue.json", Subject)
    requisites = _load(data_dir / "requisites.json", SubjectRequisites)
    baseline_students = _load(students_path, Student)

    def _current_student(student_id) -> Student | None:
        if run_dir is not None:
            state_path = Path(run_dir) / "state.json"
            if state_path.exists():
                # Not cached across calls, deliberately: this file is exactly
                # what changes between one call and the next, and the whole
                # point of reading it here is to see this call's mutations
                # from the last, not the process's first.
                return _load(state_path, Student).get(student_id)
        return baseline_students.get(student_id)

    def enrol(arguments: dict) -> str | None:
        student_id = arguments.get("student_id")
        code = arguments.get("code")
        student = _current_student(student_id)
        if student is None or code not in catalogue:
            return (
                f"cannot verify enrolment of {student_id!r} "
                f"in {code!r}: no such student or subject in this dataset."
            )

        result = evaluate_requisites(code, student, requisites)
        if result.unresolved:
            # Never permit what could not be verified. An unresolved item is
            # a distinct outcome from an unsatisfied one and must stay
            # distinguishable in the decision log.
            return (
                f"{code} could not be checked: "
                f"{', '.join(result.unresolved)} could not be interpreted. "
                f"An enrolment that cannot be verified is not permitted."
            )
        if not result.satisfied:
            return result.explanation

        unknown_enrolled = sorted(c for c in student.enrolled if c not in catalogue)
        if unknown_enrolled:
            # The same "never permit what could not be verified" rule as the
            # unresolved-requisite branch above: an enrolled code missing
            # from the catalogue cannot be priced, and scoring it as 0 credit
            # points -- as a bare `if c in catalogue` filter would -- silently
            # undercounts the session load the cap is meant to be checked
            # against. That is the fail-open direction, so it is refused
            # outright instead.
            return (
                f"cannot verify {student_id}'s current credit-point load: "
                f"{', '.join(unknown_enrolled)} "
                f"{'is' if len(unknown_enrolled) == 1 else 'are'} enrolled but not "
                f"in this dataset's catalogue. An enrolment that cannot be "
                f"verified is not permitted."
            )

        enrolled_points = sum(catalogue[c].credit_points for c in student.enrolled)
        if exceeds_credit_cap(student, catalogue[code], enrolled_points):
            return (
                f"enrolling in {code} would take this session to "
                f"{enrolled_points + catalogue[code].credit_points} credit points, "
                f"over the {student.credit_point_cap_per_session} credit point limit. "
                f"Submit an eRequest to apply for a higher load."
            )
        return None

    return {"enrol": enrol}
