import json

from enrolment.models import Subject
from enrolment.students import FEIT_DESCRIPTOR, generate_students, write_students

WHEN = "2026-08-26T00:00:00"


def catalogue(n: int = 10) -> dict[str, Subject]:
    return {
        f"3200{i}": Subject(
            code=f"3200{i}",
            name=f"Subject {i}",
            credit_points=6,
            source_url="https://example.test",
            fetched_at=WHEN,
        )
        for i in range(n)
    }


def test_generates_the_requested_count():
    students = generate_students(catalogue(), count=5, seed=0)
    assert len(students) == 5


def test_student_ids_are_unique():
    students = generate_students(catalogue(), count=20, seed=0)
    assert len({s.student_id for s in students}) == 20


def test_generation_is_deterministic_for_a_seed():
    first = generate_students(catalogue(), count=5, seed=7)
    second = generate_students(catalogue(), count=5, seed=7)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]


def test_different_seeds_give_different_cohorts():
    first = generate_students(catalogue(), count=5, seed=1)
    second = generate_students(catalogue(), count=5, seed=2)
    assert [s.model_dump() for s in first] != [s.model_dump() for s in second]


def test_completed_subjects_come_from_the_catalogue():
    subjects = catalogue()
    for student in generate_students(subjects, count=10, seed=0):
        assert student.completed_codes() <= set(subjects)


def test_students_carry_both_admission_forms():
    student = generate_students(catalogue(), count=1, seed=0)[0]
    assert "C04296" in student.admitted_to
    assert FEIT_DESCRIPTOR in student.admitted_to


def test_write_students_round_trips(tmp_path):
    students = generate_students(catalogue(), count=3, seed=0)
    path = tmp_path / "students.json"
    write_students(path, students)
    loaded = json.loads(path.read_text())
    assert set(loaded) == {s.student_id for s in students}
