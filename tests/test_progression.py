from enrolment.models import BlockItem, Course, CourseBlock
from enrolment.progression import Block, Placement, assign, expand_structure

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

    assert counted == 12
    assert sorted(p.code for p in placements) == ["X1", "X2"]
    assert len({p.block for p in placements}) == 2  # one each, not both in Options


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
