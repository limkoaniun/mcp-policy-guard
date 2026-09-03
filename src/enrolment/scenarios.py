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

from pydantic import BaseModel, field_validator

from .models import Student, Subject, SubjectRequisites
from .requisites import evaluate_requisites
from .rules import depth, parse

FAILURE_MODES = (
    "compliant",
    "unmet_academic",
    "unmet_aos",
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

    @field_validator("failure_mode")
    @classmethod
    def _known_failure_mode(cls, value: str) -> str:
        """Reject a mode `summarise()` would silently drop into its own bucket.

        FAILURE_MODES was previously declared and never referenced, so a typo
        or a newly-classified kind produced a scenario whose category nothing
        downstream knew to look for -- a population quietly missing from every
        summary that reads as a complete one.
        """
        if value not in FAILURE_MODES:
            raise ValueError(f"unknown failure mode {value!r}: expected one of {FAILURE_MODES}")
        return value


def classify_failure(result, record: SubjectRequisites | None) -> str:
    """Categorise one evaluation outcome for the compliance dataset.

    When an AND-composed rule has several simultaneously-unmet items of
    different kinds, this returns only the single highest-priority kind
    (admission > aos > credit_points > academic) — it is not an exhaustive
    account of everything unmet. `summarise()` counts should be read as "at
    least this many failed for this reason", not precise per-category tallies.

    Area-of-study failures get their own mode rather than folding into
    `unmet_academic`: UTS publishes them behind an "Admission into ..." prefix,
    which is exactly where they were being misread as admission requisites, so
    that population needs to stay visible in `summarise()`.
    """
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
    if "aos" in kinds:
        return "unmet_aos"
    if "credit_points" in kinds:
        return "unmet_credit_points"
    return "unmet_academic"


def _rule_depth(record: SubjectRequisites | None) -> int:
    """Nesting depth of the published *requisite* rule specifically.

    `0` means "no requisite rule is published for this subject" — that
    includes subjects with no rules at all and subjects that publish only an
    anti-requisite rule. Anti-requisite depth is not folded into this value;
    `failure_mode` already separates anti-requisite cases from requisite
    ones, so those two populations should not be merged under one number.
    """
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


def read_jsonl(path) -> list[ComplianceScenario]:
    """Read back what `write_jsonl` wrote.

    Validation is not bypassed: every line goes through `ComplianceScenario`,
    so a suite carrying a failure mode nothing downstream knows about is
    refused here rather than becoming a population quietly missing from every
    summary that reads as complete.
    """
    return [
        ComplianceScenario.model_validate(json.loads(line))
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
