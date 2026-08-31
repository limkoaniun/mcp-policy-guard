from enrolment.models import BlockItem, Course, CourseBlock
from enrolment.progression import expand_structure

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
