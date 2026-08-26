import json

import pytest

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


MAJORS = ["MAJ02093", "MAJ02902", "MAJ03518"]
SUB_MAJORS = ["SMJ02038", "SMJ02016"]


def test_a_single_major_is_still_shared_by_every_student():
    students = generate_students(catalogue(), count=5, seed=0, major="MAJ02093")
    assert {s.major for s in students} == {"MAJ02093"}
    assert {s.sub_major for s in students} == {None}


def test_a_single_major_produces_exactly_the_cohort_it_always_did():
    """The new draws must not shift the rest of the cohort for a given seed."""
    with_major = generate_students(catalogue(), count=5, seed=3, major="MAJ02093")
    without = generate_students(catalogue(), count=5, seed=3)
    assert [s.completed_codes() for s in with_major] == [s.completed_codes() for s in without]
    assert [s.enrolled for s in with_major] == [s.enrolled for s in without]


def test_a_sequence_of_majors_is_sampled_per_student():
    students = generate_students(catalogue(), count=30, seed=0, major=MAJORS)
    assigned = {s.major for s in students}
    assert assigned <= set(MAJORS)
    assert len(assigned) > 1, "a cohort drawn from a pool must actually vary"


def test_sub_majors_can_be_sampled_too():
    students = generate_students(
        catalogue(), count=30, seed=0, major=MAJORS, sub_major=SUB_MAJORS
    )
    assert {s.sub_major for s in students} <= set(SUB_MAJORS)
    assert len({s.sub_major for s in students}) > 1
    assert len({(s.major, s.sub_major) for s in students}) > 2


def test_sampling_from_a_sequence_is_deterministic_for_a_seed():
    first = generate_students(catalogue(), count=10, seed=11, major=MAJORS, sub_major=SUB_MAJORS)
    second = generate_students(catalogue(), count=10, seed=11, major=MAJORS, sub_major=SUB_MAJORS)
    assert [(s.major, s.sub_major) for s in first] == [
        (s.major, s.sub_major) for s in second
    ]
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]


def test_different_seeds_assign_different_areas_of_study():
    first = generate_students(catalogue(), count=10, seed=1, major=MAJORS)
    second = generate_students(catalogue(), count=10, seed=2, major=MAJORS)
    assert [s.major for s in first] != [s.major for s in second]


def test_a_tuple_of_majors_works_as_well_as_a_list():
    students = generate_students(catalogue(), count=10, seed=5, major=tuple(MAJORS))
    assert {s.major for s in students} <= set(MAJORS)


@pytest.mark.parametrize("field", ["major", "sub_major"])
def test_an_empty_pool_is_rejected_rather_than_silently_giving_no_variance(field):
    with pytest.raises(ValueError, match=field):
        generate_students(catalogue(), count=3, seed=0, **{field: []})


def test_write_students_round_trips(tmp_path):
    students = generate_students(catalogue(), count=3, seed=0)
    path = tmp_path / "students.json"
    write_students(path, students)
    loaded = json.loads(path.read_text())
    assert set(loaded) == {s.student_id for s in students}
