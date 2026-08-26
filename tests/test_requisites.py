import pytest

from enrolment.models import (
    CompletedSubject,
    RequisiteItem,
    Student,
    Subject,
    SubjectRequisites,
)
from enrolment.requisites import evaluate_requisites, exceeds_credit_cap, resolve_item

WHEN = "2026-08-26T00:00:00"


def subject_item(label: str, code: str) -> RequisiteItem:
    return RequisiteItem(
        label=label, kind="subject", type="academic", text=f"{code} Something", subject=code
    )


def requisites(code: str, rule: str, items: dict[str, RequisiteItem], **kwargs):
    return {
        code: SubjectRequisites(
            code=code,
            requisite_rule=rule,
            requisite_items=items,
            source_url="https://example.test",
            fetched_at=WHEN,
            **kwargs,
        )
    }


def student(**kwargs) -> Student:
    defaults = {"student_id": "S1", "course": "C04296", "admitted_to": ["C04296"]}
    defaults.update(kwargs)
    return Student(**defaults)


def completed(*codes: str) -> list[CompletedSubject]:
    return [CompletedSubject(code=code, session="2025-AUT", credit_points=6) for code in codes]


def test_satisfied_when_required_subject_completed():
    reqs = requisites("32146", "(1)", {"1": subject_item("1", "31250")})
    result = evaluate_requisites("32146", student(completed=completed("31250")), reqs)
    assert result.satisfied is True
    assert result.unmet == []


def test_unsatisfied_reports_minimal_unmet_set():
    reqs = requisites(
        "32146",
        "(1 AND (2 OR 2a))",
        {
            "1": subject_item("1", "31250"),
            "2": subject_item("2", "48024"),
            "2a": subject_item("2a", "43031"),
        },
    )
    result = evaluate_requisites("32146", student(completed=completed("31250")), reqs)
    assert result.satisfied is False
    assert len(result.unmet) == 1
    assert result.unmet[0] in {"2", "2a"}


def test_explanation_names_the_missing_requisite():
    reqs = requisites("32146", "(1)", {"1": subject_item("1", "31250")})
    result = evaluate_requisites("32146", student(), reqs)
    assert "31250" in result.explanation


def test_subject_with_no_requisite_record_is_satisfied():
    result = evaluate_requisites("42890", student(), {})
    assert result.satisfied is True


def test_antirequisite_blocks_enrolment():
    reqs = requisites(
        "31251",
        "(1)",
        {"1": subject_item("1", "48024")},
        antirequisite_rule="(1 AND 2)",
        antirequisite_items={
            "1": subject_item("1", "31473"),
            "2": subject_item("2", "32510"),
        },
    )
    blocked = evaluate_requisites(
        "31251", student(completed=completed("48024", "31473", "32510")), reqs
    )
    assert blocked.satisfied is False
    assert blocked.blocked_by_antirequisite is True

    allowed = evaluate_requisites("31251", student(completed=completed("48024", "31473")), reqs)
    assert allowed.satisfied is True
    assert allowed.blocked_by_antirequisite is False


def test_credit_point_requisite_sums_completed_points():
    item = RequisiteItem(
        label="1",
        kind="credit_points",
        type="academic",
        text="Must have completed at least 48 credit points",
        credit_points=48,
    )
    reqs = requisites("32558", "(1)", {"1": item})
    eight = student(completed=completed(*[f"3200{n}" for n in range(8)]))
    assert evaluate_requisites("32558", eight, reqs).satisfied is True
    four = student(completed=completed("32001", "32002", "32003", "32004"))
    assert evaluate_requisites("32558", four, reqs).satisfied is False


def test_admission_requisite_matches_course_code():
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="Admission into C04296 Master of Information Technology (Extension)",
        course="C04296",
        admission_descriptor="C04296 Master of Information Technology (Extension)",
    )
    reqs = requisites("32531", "(1)", {"1": item})
    assert evaluate_requisites("32531", student(admitted_to=["C04296"]), reqs).satisfied is True
    assert evaluate_requisites("32531", student(admitted_to=["C04405"]), reqs).satisfied is False


def test_admission_requisite_matches_free_text_descriptor():
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="Admission into Master's Coursework courses owned by FEIT",
        admission_descriptor="Master's Coursework courses owned by FEIT",
    )
    reqs = requisites("32933", "(1)", {"1": item})
    enrolled = student(admitted_to=["Master's Coursework courses owned by FEIT"])
    assert evaluate_requisites("32933", enrolled, reqs).satisfied is True


def test_area_of_study_requisite_matches_major():
    item = RequisiteItem(
        label="1",
        kind="aos",
        type="academic",
        text="MAJ02902 36cp Information Technology Major MBA",
        aos_code="MAJ02902",
    )
    reqs = requisites("32531", "(1)", {"1": item})
    assert evaluate_requisites("32531", student(major="MAJ02902"), reqs).satisfied is True
    assert evaluate_requisites("32531", student(major="MAJ02093"), reqs).satisfied is False


def test_unparsed_item_is_reported_not_silently_passed():
    item = RequisiteItem(label="1", kind="unparsed", type="other", text="wording we cannot read")
    reqs = requisites("99999", "(1)", {"1": item})
    result = evaluate_requisites("99999", student(), reqs)
    assert result.satisfied is False
    assert result.unresolved == ["1"]


def test_label_missing_from_item_table_is_reported_as_unresolved():
    reqs = requisites("99999", "(1 AND 2)", {"1": subject_item("1", "31250")})
    result = evaluate_requisites("99999", student(completed=completed("31250")), reqs)
    assert result.satisfied is False
    assert "2" in result.unresolved


def test_resolve_item_explains_itself():
    outcome = resolve_item(subject_item("1", "31250"), student())
    assert outcome.satisfied is False
    assert "31250" in outcome.reason


@pytest.mark.parametrize(
    "already,points,expected", [(18, 6, False), (24, 6, True), (0, 6, False)]
)
def test_credit_cap(already, points, expected):
    enrolled = student(credit_point_cap_per_session=24)
    enrolled.enrolled = []
    subject = Subject(
        code="32146",
        name="X",
        credit_points=points,
        source_url="https://example.test",
        fetched_at=WHEN,
    )
    enrolled_points = already
    enrolled.completed = []
    assert (
        exceeds_credit_cap(enrolled, subject, enrolled_points=enrolled_points) is expected
    )


# --- Additional tests beyond the brief -------------------------------------
#
# These cover two gaps the brief's own sample implementation left open (see
# task-5-report.md for the full rationale). Both are cases where an
# unresolvable item could otherwise silently produce a "safe-looking" wrong
# answer: an unparsed anti-requisite item defaulting to "not held" (so a
# student who should be blocked is let through undetected), and a
# malformed credit_points/aos item whose missing field would otherwise
# collapse to a trivially-true comparison.


def test_unparsed_antirequisite_item_is_reported_and_does_not_silently_clear():
    """An anti-requisite item that cannot be interpreted must be flagged.

    If it silently resolved to "not held" (as a naive default-False would),
    the anti-requisite AND clause would never trigger, and a student who
    may actually hold the antirequisite would be waved through with no
    trace that the determination was unreliable.
    """
    reqs = requisites(
        "31251",
        "(1)",
        {"1": subject_item("1", "48024")},
        antirequisite_rule="(1 AND 2)",
        antirequisite_items={
            "1": subject_item("1", "31473"),
            "2": RequisiteItem(label="2", kind="unparsed", type="other", text="unreadable"),
        },
    )
    result = evaluate_requisites(
        "31251", student(completed=completed("48024", "31473")), reqs
    )
    assert result.blocked_by_antirequisite is False
    assert "antirequisite:2" in result.unresolved


def test_credit_points_item_with_no_threshold_is_unresolved_not_satisfied():
    item = RequisiteItem(
        label="1",
        kind="credit_points",
        type="academic",
        text="credit point wording we could not parse a number from",
        credit_points=None,
    )
    reqs = requisites("32558", "(1)", {"1": item})
    result = evaluate_requisites("32558", student(), reqs)
    assert result.satisfied is False
    assert "1" in result.unresolved


def test_aos_item_with_no_code_is_unresolved_not_satisfied_by_absent_major():
    """A malformed aos item (no aos_code) must not match a student with no major.

    Both are `None`; comparing them directly would make `None in {None}` true
    and let an unresolvable item pass as satisfied purely because the
    student also happens to have no major recorded.
    """
    item = RequisiteItem(
        label="1",
        kind="aos",
        type="academic",
        text="area of study wording we could not parse a code from",
        aos_code=None,
    )
    reqs = requisites("32531", "(1)", {"1": item})
    result = evaluate_requisites("32531", student(major=None, sub_major=None), reqs)
    assert result.satisfied is False
    assert "1" in result.unresolved


def test_admission_item_with_no_payload_is_unresolved_not_satisfied_by_blank_entry():
    """A garbled admission item (no course, no descriptor) must not read satisfied.

    A naive implementation normalises the missing descriptor to "" and checks
    whether "" is among the student's normalised `admitted_to` entries. A
    blank or whitespace-only entry in `admitted_to` then makes that check
    true, so an item whose payload could not be read at all comes back
    satisfied -- the single most damaging outcome this module can produce.
    """
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="garbled",
    )
    reqs = requisites("31251", "(1)", {"1": item})
    result = evaluate_requisites(
        "31251", student(admitted_to=["C04296", "  "]), reqs
    )
    assert result.satisfied is False
    assert "1" in result.unresolved


def test_subject_item_with_no_code_is_unresolved_not_satisfied():
    item = RequisiteItem(
        label="1",
        kind="subject",
        type="academic",
        text="subject wording we could not parse a code from",
        subject=None,
    )
    reqs = requisites("32146", "(1)", {"1": item})
    result = evaluate_requisites("32146", student(), reqs)
    assert result.satisfied is False
    assert "1" in result.unresolved


def test_admission_descriptor_matches_with_differing_case_and_padding():
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="Admission into Master's Coursework courses owned by FEIT",
        admission_descriptor="Master's Coursework courses owned by FEIT",
    )
    reqs = requisites("32933", "(1)", {"1": item})
    enrolled = student(admitted_to=["  MASTER'S coursework   COURSES owned by feit  "])
    assert evaluate_requisites("32933", enrolled, reqs).satisfied is True


def test_admission_course_code_matches_with_differing_case():
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="Admission into C04296 Master of Information Technology (Extension)",
        course="C04296",
        admission_descriptor="C04296 Master of Information Technology (Extension)",
    )
    reqs = requisites("32531", "(1)", {"1": item})
    assert evaluate_requisites("32531", student(admitted_to=["c04296"]), reqs).satisfied is True


def test_admission_item_with_blank_course_string_is_unresolved_not_satisfied():
    """Empty-string course (not None) must not slip past the unresolvable gate.

    A naive `is None` check on `item.course` treats `course=""` as usable
    payload, and normalising `""` to `""` then matching it against a blank
    or whitespace-only `admitted_to` entry reports the item satisfied -- the
    same critical failure shape as a missing course, through a different
    door.
    """
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="garbled",
        course="",
    )
    reqs = requisites("31251", "(1)", {"1": item})
    result = evaluate_requisites(
        "31251", student(admitted_to=["C04296", "   "]), reqs
    )
    assert result.satisfied is False
    assert "1" in result.unresolved


def test_subject_item_with_blank_code_string_is_unresolved_not_satisfied():
    item = RequisiteItem(
        label="1",
        kind="subject",
        type="academic",
        text="subject wording we could not parse a code from",
        subject="",
    )
    reqs = requisites("32146", "(1)", {"1": item})
    result = evaluate_requisites("32146", student(), reqs)
    assert result.satisfied is False
    assert "1" in result.unresolved


def test_aos_item_with_blank_code_string_is_unresolved_not_satisfied():
    item = RequisiteItem(
        label="1",
        kind="aos",
        type="academic",
        text="area of study wording we could not parse a code from",
        aos_code="",
    )
    reqs = requisites("32531", "(1)", {"1": item})
    result = evaluate_requisites("32531", student(), reqs)
    assert result.satisfied is False
    assert "1" in result.unresolved
