import pytest
from pydantic import ValidationError

from enrolment.models import (
    CompletedSubject,
    RequisiteItem,
    Student,
    Subject,
    SubjectRequisites,
)
from enrolment.scenarios import (
    FAILURE_MODES,
    ComplianceScenario,
    generate_scenarios,
    summarise,
    write_jsonl,
)

WHEN = "2026-08-26T00:00:00"


def subject(code: str) -> Subject:
    return Subject(
        code=code, name=f"Subject {code}", credit_points=6, source_url="u", fetched_at=WHEN
    )


CATALOGUE = {
    code: subject(code) for code in ["32146", "42890", "31251", "99001", "99002", "99003"]
}

REQUISITES = {
    "32146": SubjectRequisites(
        code="32146",
        requisite_rule="(1 AND (2 OR 2a))",
        requisite_items={
            "1": RequisiteItem(
                label="1", kind="subject", type="academic", text="31250 Intro", subject="31250"
            ),
            "2": RequisiteItem(
                label="2", kind="subject", type="academic", text="48024 Prog 2", subject="48024"
            ),
            "2a": RequisiteItem(
                label="2a", kind="subject", type="academic", text="43031 Python", subject="43031"
            ),
        },
        source_url="u",
        fetched_at=WHEN,
    ),
    "42890": SubjectRequisites(code="42890", source_url="u", fetched_at=WHEN),
    "31251": SubjectRequisites(
        code="31251",
        antirequisite_rule="(1)",
        antirequisite_items={
            "1": RequisiteItem(
                label="1", kind="subject", type="academic", text="31473 DSPP", subject="31473"
            )
        },
        source_url="u",
        fetched_at=WHEN,
    ),
    "99001": SubjectRequisites(
        code="99001",
        requisite_rule="(1)",
        requisite_items={
            "1": RequisiteItem(
                label="1", kind="unparsed", text="wording we cannot read"
            ),
        },
        source_url="u",
        fetched_at=WHEN,
    ),
    "99002": SubjectRequisites(
        code="99002",
        requisite_rule="(1)",
        requisite_items={
            "1": RequisiteItem(
                label="1",
                kind="admission",
                type="admission",
                text="admission to Graduate Diploma in Advanced IT",
                admission_descriptor="Graduate Diploma in Advanced IT",
            ),
        },
        source_url="u",
        fetched_at=WHEN,
    ),
    "99003": SubjectRequisites(
        code="99003",
        requisite_rule="(1)",
        requisite_items={
            "1": RequisiteItem(
                label="1",
                kind="credit_points",
                type="academic",
                text="96 credit points",
                credit_points=96,
            ),
        },
        source_url="u",
        fetched_at=WHEN,
    ),
    "99004": SubjectRequisites(
        code="99004",
        requisite_rule="(1)",
        requisite_items={
            "1": RequisiteItem(
                label="1",
                kind="aos",
                # As UTS actually publishes it: an aos requisite behind an
                # "Admission into ..." prefix, typed as an admission requisite.
                type="admission",
                text="Admission into MAJ02902 36cp Information Technology Major MBA",
                aos_code="MAJ02902",
                admission_descriptor="MAJ02902 36cp Information Technology Major MBA",
            ),
        },
        source_url="u",
        fetched_at=WHEN,
    ),
}
CATALOGUE["99004"] = subject("99004")


def student(student_id: str, *completed: str) -> Student:
    return Student(
        student_id=student_id,
        course="C04296",
        admitted_to=["C04296"],
        completed=[
            CompletedSubject(code=code, session="2025-AUT", credit_points=6)
            for code in completed
        ],
    )


def test_generates_one_scenario_per_student_subject_pair():
    students = [student("S1"), student("S2", "31250", "48024")]
    scenarios = generate_scenarios(students, CATALOGUE, REQUISITES)
    assert len(scenarios) == len(students) * len(CATALOGUE)


def test_expected_satisfied_matches_the_evaluator():
    met = student("S1", "31250", "48024")
    scenarios = generate_scenarios([met], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["32146"].expected_satisfied is True
    assert by_code["42890"].expected_satisfied is True


def test_unmet_academic_requisite_is_labelled():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["32146"].failure_mode == "unmet_academic"
    assert by_code["32146"].expected_satisfied is False


def test_antirequisite_violation_is_labelled():
    scenarios = generate_scenarios([student("S1", "31473")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["31251"].failure_mode == "antirequisite"


def test_compliant_cases_are_labelled():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["42890"].failure_mode == "compliant"


def test_rule_depth_is_recorded_for_stratification():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["32146"].rule_depth >= 2
    assert by_code["42890"].rule_depth == 0


def test_scenario_ids_are_unique():
    students = [student("S1"), student("S2", "31250")]
    scenarios = generate_scenarios(students, CATALOGUE, REQUISITES)
    assert len({s.scenario_id for s in scenarios}) == len(scenarios)


def test_summarise_counts_by_failure_mode():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    counts = summarise(scenarios)
    assert counts["unmet_academic"] == 1
    assert counts["compliant"] >= 1


def test_write_jsonl_emits_one_object_per_line(tmp_path):
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    path = tmp_path / "compliance.jsonl"
    write_jsonl(path, scenarios)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == len(scenarios)


def test_unresolvable_requisite_is_labelled_unresolved():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["99001"].failure_mode == "unresolved"
    assert by_code["99001"].expected_satisfied is False


def test_unmet_admission_requisite_is_labelled():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["99002"].failure_mode == "unmet_admission"


def test_unmet_credit_points_requisite_is_labelled():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["99003"].failure_mode == "unmet_credit_points"


def test_unmet_area_of_study_requisite_has_its_own_failure_mode():
    """It used to fold into unmet_academic, hiding the population entirely.

    Area-of-study requisites are exactly the ones that were being misread as
    admission requisites, so they have to stay visible in `summarise()`.
    """
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["99004"].failure_mode == "unmet_aos"
    assert by_code["99004"].expected_satisfied is False
    assert summarise(scenarios)["unmet_aos"] == 1


def test_a_student_holding_the_major_is_compliant():
    holder = student("S2")
    holder.major = "MAJ02902"
    scenarios = generate_scenarios([holder], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["99004"].failure_mode == "compliant"


def test_every_generated_failure_mode_is_a_declared_one():
    scenarios = generate_scenarios([student("S1"), student("S2", "31473")], CATALOGUE, REQUISITES)
    assert {s.failure_mode for s in scenarios} <= set(FAILURE_MODES)


def test_an_undeclared_failure_mode_is_rejected_at_construction():
    """FAILURE_MODES was declared and never referenced.

    A mode nothing downstream knows about would land in `summarise()` as a
    category no reader is looking for -- a population quietly missing from a
    summary that reads as complete.
    """
    with pytest.raises(ValidationError, match="unknown failure mode"):
        ComplianceScenario(
            scenario_id="S1-32146",
            student_id="S1",
            subject_code="32146",
            failure_mode="unmet_prerequisite",
            rule_depth=1,
            expected_satisfied=False,
            explanation="",
        )
