from enrolment.models import (
    CompletedSubject,
    RequisiteItem,
    Student,
    SubjectRequisites,
)


def test_requisite_item_keeps_raw_text_for_unparsed_kind():
    item = RequisiteItem(label="1", kind="unparsed", text="something we did not recognise")
    assert item.text == "something we did not recognise"
    assert item.subject is None


def test_subject_requisites_defaults_to_no_rules():
    req = SubjectRequisites(
        code="42890", source_url="https://example.test", fetched_at="2026-08-26"
    )
    assert req.requisite_rule is None
    assert req.requisite_items == {}
    assert req.antirequisite_rule is None


def test_student_round_trips_through_json():
    student = Student(
        student_id="S1000001",
        course="C04296",
        admitted_to=["C04296", "Master's Coursework courses owned by FEIT"],
        major="MAJ02093",
        completed=[CompletedSubject(code="32541", session="2025-AUT", credit_points=6)],
        enrolled=["32144"],
    )
    restored = Student.model_validate_json(student.model_dump_json())
    assert restored == student
    assert restored.credit_point_cap_per_session == 24


def test_student_completed_codes_helper():
    student = Student(
        student_id="S1",
        course="C04296",
        completed=[
            CompletedSubject(code="32541", session="2025-AUT", credit_points=6),
            CompletedSubject(code="43030", session="2025-SPR", credit_points=6),
        ],
    )
    assert student.completed_codes() == {"32541", "43030"}
    assert student.completed_credit_points() == 12
