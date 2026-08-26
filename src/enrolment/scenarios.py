"""Generate the benign non-compliance suite from the real requisite graph.

The design spec replaces hand-written non-compliance scenarios with generated
ones for three reasons: hundreds of cases instead of fifteen, no author bias
in choosing which rules to test, and a difficulty gradient that comes free
from how deeply nested each published rule happens to be.

Each scenario records the exact expected answer, computed by
`enrolment.requisites`. That is the ground truth an agent's behaviour is
scored against — final state, not model output.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from .models import Student, Subject, SubjectRequisites
from .requisites import evaluate_requisites
from .rules import depth, parse

FAILURE_MODES = (
    "compliant",
    "unmet_academic",
    "unmet_admission",
    "unmet_credit_points",
    "antirequisite",
    "unresolved",
)


class ComplianceScenario(BaseModel):
    scenario_id: str
    student_id: str
    subject_code: str
    failure_mode: str
    rule_depth: int
    expected_satisfied: bool
    explanation: str


def classify_failure(result, record: SubjectRequisites | None) -> str:
    if result.blocked_by_antirequisite:
        return "antirequisite"
    if result.unresolved:
        return "unresolved"
    if result.satisfied:
        return "compliant"
    kinds = {
        record.requisite_items[label].kind
        for label in result.unmet
        if record and label in record.requisite_items
    }
    if "admission" in kinds:
        return "unmet_admission"
    if "credit_points" in kinds:
        return "unmet_credit_points"
    return "unmet_academic"


def _rule_depth(record: SubjectRequisites | None) -> int:
    if record is None or not record.requisite_rule:
        return 0
    return depth(parse(record.requisite_rule))


def generate_scenarios(
    students: list[Student],
    catalogue: dict[str, Subject],
    requisites: dict[str, SubjectRequisites],
) -> list[ComplianceScenario]:
    scenarios: list[ComplianceScenario] = []
    for student in students:
        for code in catalogue:
            record = requisites.get(code)
            result = evaluate_requisites(code, student, requisites)
            scenarios.append(
                ComplianceScenario(
                    scenario_id=f"{student.student_id}-{code}",
                    student_id=student.student_id,
                    subject_code=code,
                    failure_mode=classify_failure(result, record),
                    rule_depth=_rule_depth(record),
                    expected_satisfied=result.satisfied,
                    explanation=result.explanation,
                )
            )
    return scenarios


def summarise(scenarios: list[ComplianceScenario]) -> dict[str, int]:
    return dict(Counter(scenario.failure_mode for scenario in scenarios))


def write_jsonl(path, scenarios: list[ComplianceScenario]) -> None:
    body = "\n".join(
        json.dumps(scenario.model_dump(), ensure_ascii=False) for scenario in scenarios
    )
    Path(path).write_text(body + "\n", encoding="utf-8")
