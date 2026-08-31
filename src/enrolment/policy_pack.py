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


def build_validators(data_dir, students_path) -> dict:
    """Validators keyed by tool name, closed over the committed dataset."""
    data_dir = Path(data_dir)
    catalogue = _load(data_dir / "catalogue.json", Subject)
    requisites = _load(data_dir / "requisites.json", SubjectRequisites)
    students = _load(students_path, Student)

    def enrol(arguments: dict) -> str | None:
        student = students.get(arguments.get("student_id"))
        code = arguments.get("code")
        if student is None or code not in catalogue:
            return (
                f"cannot verify enrolment of {arguments.get('student_id')!r} "
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

        enrolled_points = sum(
            catalogue[c].credit_points for c in student.enrolled if c in catalogue
        )
        if exceeds_credit_cap(student, catalogue[code], enrolled_points):
            return (
                f"enrolling in {code} would take this session to "
                f"{enrolled_points + catalogue[code].credit_points} credit points, "
                f"over the {student.credit_point_cap_per_session} credit point limit. "
                f"Submit an eRequest to apply for a higher load."
            )
        return None

    return {"enrol": enrol}
