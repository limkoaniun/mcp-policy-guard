import pytest

from enrolment.models import BlockItem, Course, CourseBlock, Student, Subject
from enrolment.progression import (
    Block,
    MajorComparison,
    Placement,
    assign,
    compare_major,
    expand_structure,
    offered_in,
    session_options,
    summarise_plan,
)

WHEN = "2026-08-31T00:00:00"


def items(codes, item_type="subject"):
    return [
        BlockItem(code=c, name=f"Subject {c}", credit_points=6, item_type=item_type)
        for c in codes
    ]


def block(title, cp, codes=(), item_type="subject", children=()):
    return CourseBlock(
        title=title,
        credit_points=cp,
        items=items(codes, item_type),
        children=list(children),
    )


def course_of(*blocks, code="C04296", cp=96):
    return Course(
        code=code,
        title="Test Course",
        credit_points=cp,
        blocks=list(blocks),
        source_url="https://example.test",
        fetched_at=WHEN,
    )


def test_expand_structure_picks_only_the_chosen_major():
    course = course_of(
        block("Core", 12, ["C1", "C2"]),
        block("Majors", 36, ["MAJ01", "MAJ02"], item_type="major"),
    )
    aos = {
        "MAJ01": course_of(block("Alpha", 36, ["A1", "A2"]), code="MAJ01", cp=36),
        "MAJ02": course_of(block("Beta", 36, ["B1"]), code="MAJ02", cp=36),
    }

    blocks = expand_structure(course, aos, major="MAJ01", sub_major=None)

    assert [b.path for b in blocks] == ["Core", "Major:Test Course/Alpha"]
    assert [b.kind for b in blocks] == ["core", "major"]
    assert blocks[1].eligible == frozenset({"A1", "A2"})


def test_expand_structure_expands_a_stream_reference_in_place():
    electives = course_of(block("Electives", 12, ["E1", "E2"]), code="CBK01", cp=12)
    course = course_of(
        block(
            "Project and Electives",
            18,
            children=[
                block("Project", 6, ["P1", "P2"]),
                block("Electives", 12, ["CBK01"], item_type="stream"),
            ],
        )
    )

    blocks = expand_structure(course, {"CBK01": electives}, major=None, sub_major=None)

    assert [(b.path, b.quota, b.kind) for b in blocks] == [
        ("Project and Electives/Project", 6, "project"),
        ("Elective:CBK01/Electives", 12, "elective"),
    ]


def test_expand_structure_omits_a_choice_point_left_unchosen():
    course = course_of(
        block("Core", 12, ["C1"]),
        block("Sub-Majors", 24, ["SMJ01"], item_type="sub_major"),
    )
    aos = {"SMJ01": course_of(block("Options", 24, ["S1"]), code="SMJ01", cp=24)}

    blocks = expand_structure(course, aos, major=None, sub_major=None)

    assert [b.path for b in blocks] == ["Core"]


def test_expand_structure_keys_core_off_quota_not_title():
    """Core/project split is keyed off block structure, not title."""
    course = course_of(
        # Block titled 'Custom Core' with quota matching item total = core
        block("Custom Core", 12, ["C1", "C2"]),  # quota=12, items=2*6=12 => core
        # Block titled 'Custom Project' with quota < item total = project
        block("Custom Project", 6, ["P1", "P2"]),  # quota=6, items=2*6=12 => project
    )

    blocks = expand_structure(course, {}, major=None, sub_major=None)

    # First block should be core despite being titled 'Custom Core'
    assert blocks[0].kind == "core"
    # Second block should be project despite being titled 'Custom Project'
    assert blocks[1].kind == "project"


def test_assign_counts_a_subject_once_across_dual_listed_blocks():
    blocks = [
        Block(path="Options", quota=12, eligible=frozenset({"X1", "X2"}), kind="major"),
        Block(path="Advanced", quota=12, eligible=frozenset({"X1", "X2"}), kind="major"),
    ]

    counted, placements = assign({"X1": 6, "X2": 6}, blocks)

    # Total counted credit is maximised
    assert counted == 12
    # Both subjects appear in placements (single-count rule satisfied)
    assert sorted(p.code for p in placements) == ["X1", "X2"]
    # Each subject is counted exactly once
    assert sum(1 for p in placements if p.code == "X1") == 1
    assert sum(1 for p in placements if p.code == "X2") == 1
    # No block receives more than its quota
    for block in blocks:
        block_total = sum(p.credit_points for p in placements if p.block == block.path)
        assert block_total <= block.quota
    # Placement sum equals counted total (invariant: all counted credit is in placements)
    assert sum(p.credit_points for p in placements) == counted
    # Distribution between equally-ranked blocks is unspecified by the algorithm


def test_assign_beats_first_fit_when_order_would_strand_a_subject():
    """The spec's minimal case: 32933 fits only the project block, 32040 fits both.

    First-fit puts 32040 in the project block, fills the 6cp quota, and strands
    32933 -- reporting 6cp. The optimal assignment counts 12cp.
    """
    blocks = [
        Block(path="Project", quota=6, eligible=frozenset({"32933", "32040"}), kind="project"),
        Block(path="Electives", quota=12, eligible=frozenset({"32040"}), kind="elective"),
    ]

    counted, placements = assign({"32933": 6, "32040": 6}, blocks)

    assert counted == 12
    assert Placement(code="32933", block="Project", credit_points=6) in placements
    assert Placement(code="32040", block="Electives", credit_points=6) in placements


def test_assign_prefers_the_constrained_block_when_totals_tie():
    """One subject, two blocks with room. Core outranks elective."""
    blocks = [
        Block(path="Electives", quota=12, eligible=frozenset({"Y1"}), kind="elective"),
        Block(path="Core", quota=12, eligible=frozenset({"Y1"}), kind="core"),
    ]

    counted, placements = assign({"Y1": 6}, blocks)

    assert counted == 6
    assert placements == [Placement(code="Y1", block="Core", credit_points=6)]


def test_assign_leaves_ineligible_subjects_uncounted():
    blocks = [Block(path="Core", quota=12, eligible=frozenset({"Z1"}), kind="core")]

    counted, placements = assign({"Z1": 6, "Z9": 6}, blocks)

    assert counted == 6
    assert [p.code for p in placements] == ["Z1"]


def test_assign_handles_no_blocks():
    assert assign({"Z1": 6}, []) == (0, [])


def test_assign_handles_no_subjects():
    blocks = [Block(path="Core", quota=12, eligible=frozenset({"Z1"}), kind="core")]
    assert assign({}, blocks) == (0, [])


def test_assign_never_splits_a_subject_across_blocks():
    """A subject must be counted wholly in one block or not at all, never split.

    One 12cp subject eligible for two 6cp-quota blocks. The old flow-based solver
    returned two 6cp placements (split). The correct whole-subject solver counts
    it 0cp (since it cannot fit wholly in either block), or assigns it wholly if
    a larger block is available.
    """
    blocks = [
        Block(path="Block1", quota=6, eligible=frozenset({"S1"}), kind="major"),
        Block(path="Block2", quota=6, eligible=frozenset({"S1"}), kind="major"),
    ]

    counted, placements = assign({"S1": 12}, blocks)

    # A 12cp subject cannot fit wholly in either 6cp block, so it is uncounted.
    assert counted == 0
    assert placements == []
    # Crucially: S1 is never split into two 6cp placements.


def test_assign_is_exact_not_merely_feasible():
    """Whole-subject assignment differs from divisible (split) packing.

    One block (quota 9) eligible for two 6cp subjects. A divisible solver would
    return 9 (first subject wholly, second subject split: 3cp of its 6cp).
    Whole-subject assignment must return 6, because the second subject cannot
    fit wholly in the remaining 3cp, so only one subject is counted.
    """
    blocks = [
        Block(path="Shared", quota=9, eligible=frozenset({"S1", "S2"}), kind="core"),
    ]

    counted, placements = assign({"S1": 6, "S2": 6}, blocks)

    # Exactly one 6cp subject fits wholly; the other does not fit.
    assert counted == 6
    assert len(placements) == 1
    assert placements[0].credit_points == 6


def student_of(major=None, sub_major=None, completed=()):
    return Student(
        student_id="S1",
        course="C04296",
        admitted_to=["C04296"],
        major=major,
        sub_major=sub_major,
        completed=[
            {"code": c, "session": "2025-AUT", "credit_points": 6} for c in completed
        ],
    )


def test_summarise_plan_reports_the_completion_gap():
    course = course_of(
        block("Core", 12, ["C1", "C2"]),
        block("Majors", 36, ["MAJ01"], item_type="major"),
        cp=48,
    )
    aos = {"MAJ01": course_of(block("Alpha", 36, ["A1", "A2"]), code="MAJ01", cp=36)}

    summary = summarise_plan(student_of("MAJ01", None, ["C1", "A1"]), course, aos)

    assert summary.counted == 12
    assert summary.remaining_total == 36
    assert summary.counted_in_major == 6
    assert summary.remaining_in_major == 30
    assert summary.remaining_by_block == {"Core": 6, "Major:Test Course/Alpha": 30}
    assert summary.unplaced == []


def test_summarise_plan_lists_subjects_that_count_nowhere():
    course = course_of(block("Core", 12, ["C1"]), cp=12)

    summary = summarise_plan(student_of(completed=["C1", "OUTSIDE"]), course, {})

    assert summary.counted == 6
    assert summary.unplaced == ["OUTSIDE"]


def test_summarise_plan_reports_no_major_gap_when_none_is_declared():
    course = course_of(
        block("Core", 12, ["C1"]),
        block("Majors", 36, ["MAJ01"], item_type="major"),
        cp=48,
    )
    aos = {"MAJ01": course_of(block("Alpha", 36, ["A1"]), code="MAJ01", cp=36)}

    summary = summarise_plan(student_of(None, None, ["C1"]), course, aos)

    assert summary.counted_in_major == 0
    assert summary.remaining_in_major == 0


def two_major_course():
    course = course_of(
        block("Core", 12, ["C1"]),
        block("Majors", 36, ["MAJ01", "MAJ02"], item_type="major"),
        cp=48,
    )
    aos = {
        "MAJ01": course_of(block("Alpha", 36, ["A1", "A2"]), code="MAJ01", cp=36),
        "MAJ02": course_of(block("Beta", 36, ["B1", "B2"]), code="MAJ02", cp=36),
    }
    return course, aos


def test_compare_major_reports_credit_that_carries():
    course, aos = two_major_course()
    student = student_of("MAJ01", None, ["A1", "A2"])

    comparison: MajorComparison = compare_major(student, course, aos, "MAJ02")

    assert comparison.from_major == "MAJ01"
    assert comparison.to_major == "MAJ02"
    assert comparison.counted_in_current_major == 12
    assert comparison.counted_in_target_major == 0
    assert comparison.remaining_in_target_major == 36
    assert sorted(comparison.displaced) == ["A1", "A2"]


def test_compare_major_reports_nothing_displaced_when_everything_carries():
    course = course_of(
        block("Core", 12, ["C1"]),
        block("Majors", 36, ["MAJ01", "MAJ02"], item_type="major"),
        cp=48,
    )
    aos = {
        "MAJ01": course_of(block("Alpha", 36, ["S1", "S2"]), code="MAJ01", cp=36),
        "MAJ02": course_of(block("Beta", 36, ["S1", "S2"]), code="MAJ02", cp=36),
    }
    student = student_of("MAJ01", None, ["S1", "S2"])

    comparison: MajorComparison = compare_major(student, course, aos, "MAJ02")

    assert comparison.counted_in_target_major == 12
    assert comparison.displaced == []


def test_compare_major_rejects_an_unknown_target():
    course, aos = two_major_course()

    with pytest.raises(KeyError):
        compare_major(student_of("MAJ01"), course, aos, "MAJ99")


def subject_of(code, sessions):
    return Subject(
        code=code,
        name=f"Subject {code}",
        credit_points=6,
        sessions=list(sessions),
        source_url="https://example.test",
        fetched_at=WHEN,
    )


def test_offered_in_matches_a_published_session():
    assert offered_in(subject_of("A1", ["Autumn", "Spring"]), "Spring")
    assert not offered_in(subject_of("A1", ["Autumn"]), "Spring")


def test_offered_in_is_false_when_no_sessions_are_published():
    """Absent availability is not permission: an unknown offering is not offered."""
    assert not offered_in(subject_of("A1", []), "Spring")


def test_session_options_lists_only_untaken_subjects_offered_that_session():
    course = course_of(block("Core", 12, ["C1", "C2", "C3"]), cp=12)
    catalogue = {
        "C1": subject_of("C1", ["Autumn"]),
        "C2": subject_of("C2", ["Spring"]),
        "C3": subject_of("C3", ["Autumn", "Spring"]),
    }

    options = session_options(
        student_of(completed=["C1"]), course, {}, catalogue, "Spring"
    )

    assert options == {"Core": ["C2", "C3"]}


def test_session_options_omits_a_block_that_is_already_full():
    course = course_of(block("Core", 6, ["C1", "C2"]), cp=6)
    catalogue = {
        "C1": subject_of("C1", ["Spring"]),
        "C2": subject_of("C2", ["Spring"]),
    }

    options = session_options(
        student_of(completed=["C1"]), course, {}, catalogue, "Spring"
    )

    assert options == {}


def test_session_options_skips_a_block_subject_missing_from_the_catalogue():
    """`code in catalogue` gates the lookup for exactly this case: a code
    eligible for a block but absent from the supplied catalogue is skipped,
    not a `KeyError`."""
    course = course_of(block("Core", 12, ["C1", "C2"]), cp=12)
    catalogue = {"C1": subject_of("C1", ["Spring"])}  # C2 missing

    options = session_options(student_of(), course, {}, catalogue, "Spring")

    assert options == {"Core": ["C1"]}
