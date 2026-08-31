# tests/test_personas.py
"""The persona figures asserted in context/2026-08-31-scenario-suite-design.md.

These run against the committed dataset, not fixtures. They exist so the
numbers in the spec are enforced rather than remembered: the project's own
record (context/project-plan.md) is that the recurring failure mode here was
scaffolding that was confidently wrong without saying so.
"""

import json
from pathlib import Path

import pytest

from enrolment.models import Course, Student, Subject, SubjectRequisites
from enrolment.progression import compare_major, summarise_plan
from enrolment.requisites import evaluate_requisites, exceeds_credit_cap

DATA = Path(__file__).resolve().parents[1] / "data"


def _load(name, model):
    return {k: model(**v) for k, v in json.loads((DATA / name).read_text()).items()}


@pytest.fixture(scope="module")
def personas():
    return _load("personas.json", Student)


@pytest.fixture(scope="module")
def course():
    return Course(**json.loads((DATA / "course.json").read_text()))


@pytest.fixture(scope="module")
def aos():
    return _load("aos.json", Course)


@pytest.fixture(scope="module")
def requisites():
    return _load("requisites.json", SubjectRequisites)


@pytest.fixture(scope="module")
def catalogue():
    return _load("catalogue.json", Subject)


def test_personas_are_absent_from_the_generated_student_set():
    """Hand-tuned records must not contaminate the compliance suite's statistics."""
    students = json.loads((DATA / "students.json").read_text())
    personas = json.loads((DATA / "personas.json").read_text())
    assert set(students) & set(personas) == set()


def test_persona_completed_sessions_are_chronologically_consistent(personas, requisites):
    """A record must not claim a subject was completed before its requisite."""
    order = lambda s: (s.split("-")[0], {"AUT": 0, "SPR": 1}[s.split("-")[1]])
    for persona in personas.values():
        when = {c.code: order(c.session) for c in persona.completed}
        for code, session in when.items():
            record = requisites.get(code)
            for item in (record.requisite_items.values() if record else []):
                if item.subject in when:
                    assert when[item.subject] <= session, (
                        f"{persona.student_id}: {code} completed before its "
                        f"requisite {item.subject}"
                    )


# --- S9000001: changing major (spec §4) ---

CARRY_OVER = {
    "MAJ02093": 24,  # Cybersecurity (stay)
    "MAJ02083": 24,  # Internetworking
    "MAJ02084": 0,   # Data Analytics
    "MAJ02086": 0,   # Enterprise Software Development
    "MAJ02082": 0,   # Business Information Systems
    "MAJ02085": 0,   # Interactive Media
}


@pytest.mark.parametrize("target,expected", sorted(CARRY_OVER.items()))
def test_s9000001_carry_over_by_target_major(personas, course, aos, target, expected):
    comparison = compare_major(personas["S9000001"], course, aos, target)
    assert comparison.counted_in_target_major == expected
    assert comparison.remaining_in_target_major == 36 - expected


def test_s9000001_data_analytics_cores_are_both_blocked(personas, requisites):
    """The switch also imposes a prerequisite chain: both cores need 32130."""
    for code in ("32513", "32146"):
        result = evaluate_requisites(code, personas["S9000001"], requisites)
        assert not result.satisfied
        assert "32130" in result.explanation


def test_s9000001_internetworking_subjects_clear_immediately(personas, requisites):
    for code in ("32009", "32520", "42027"):
        assert evaluate_requisites(code, personas["S9000001"], requisites).satisfied


# --- S9000002: waiving a requisite (spec §4) ---

def test_s9000002_is_short_exactly_one_requisite_for_42027(personas, requisites):
    result = evaluate_requisites("42027", personas["S9000002"], requisites)
    assert not result.satisfied
    assert result.unmet == ["2"]
    assert "32547" in result.explanation


def test_s9000002_sequencing_is_governed_by_session_availability(catalogue):
    assert catalogue["42027"].sessions == ["Autumn"]
    assert catalogue["32547"].sessions == ["Autumn", "Spring"]


# --- S9000003: blocked out of their own major (spec §4) ---

def test_s9000003_is_anti_requisite_blocked_from_the_cybersecurity_core(
    personas, requisites
):
    result = evaluate_requisites("32548", personas["S9000003"], requisites)
    assert result.blocked_by_antirequisite
    assert not result.satisfied
    assert "420108" in result.explanation


def test_s9000003_blocked_subject_is_core_to_their_declared_major(aos):
    core = next(b for b in aos["MAJ02093"].blocks if b.title == "Core")
    assert "32548" in {i.code for i in core.items}


# --- S9000004: enrolling above the full-time load (spec §4) ---

def test_s9000004_is_at_the_credit_point_cap(personas, catalogue):
    persona = personas["S9000004"]
    enrolled = sum(catalogue[c].credit_points for c in persona.enrolled)
    assert enrolled == 24
    assert exceeds_credit_cap(persona, catalogue["42035"], enrolled)


def test_s9000004_also_fails_42035_on_an_unmet_requisite(personas, requisites):
    """32548 is enrolled, not completed, and 42035 publishes it as a plain
    requisite rather than an academic corequisite."""
    result = evaluate_requisites("42035", personas["S9000004"], requisites)
    assert not result.satisfied
    assert "32548" in result.explanation


def test_s9000004_42035_is_offered_in_spring_only(catalogue):
    assert catalogue["42035"].sessions == ["Spring"]


def test_s9000004_project_subjects_both_count_under_optimal_assignment(
    personas, course, aos
):
    """The spec's minimal case (§2): first-fit would strand one of them."""
    summary = summarise_plan(personas["S9000004"], course, aos)
    placed = {p.code: p.block for p in summary.placements}
    assert "32933" in placed and "32040" in placed
    assert placed["32933"] != placed["32040"]


# --- Admission descriptor normalisation (spec §7) ---

def test_one_descriptor_wrongly_blocks_twelve_subjects(personas, requisites, catalogue):
    """The open question, as a number. Spec §7."""
    narrow = personas["S9000001"].model_copy(
        update={"admitted_to": ["C04296", "Master's Coursework courses owned by FEIT"]}
    )
    assert _admission_blocked(narrow, requisites, catalogue) == 12
    assert _admission_blocked(personas["S9000001"], requisites, catalogue) == 1


def _admission_blocked(student, requisites, catalogue) -> int:
    # Counts only `result.unmet`, never `result.unresolved` -- this silently
    # relies on the committed dataset having zero unresolvable requisite
    # items (asserted elsewhere by the build's coverage gate), since an
    # unresolved admission item would not appear in `unmet` and would make
    # this an undercount rather than the exact figure the docstring claims.
    blocked = 0
    for code in catalogue:
        result = evaluate_requisites(code, student, requisites)
        if result.satisfied or result.blocked_by_antirequisite:
            continue
        items = requisites[code].requisite_items
        if any(items[label].kind == "admission" for label in result.unmet if label in items):
            blocked += 1
    return blocked
