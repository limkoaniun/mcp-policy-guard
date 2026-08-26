import json
from pathlib import Path

import pytest

from enrolment.handbook import (
    _as_int,
    extract_page_content,
    parse_course,
    parse_subject,
    referenced_codes,
    strip_html,
)
from enrolment.models import BlockItem, Course, CourseBlock

FIXTURES = Path(__file__).parent / "fixtures"
WHEN = "2026-08-26T00:00:00"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_extract_page_content_reads_next_data_payload():
    html = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"pageContent":{"code":"12345"}}}}'
        "</script></body></html>"
    )
    assert extract_page_content(html) == {"code": "12345"}


def test_extract_page_content_rejects_page_without_payload():
    with pytest.raises(ValueError, match="__NEXT_DATA__"):
        extract_page_content("<html><body>nothing here</body></html>")


def test_strip_html_removes_markup_and_unescapes():
    assert strip_html("<p>Secure &amp; sound</p>") == "Secure & sound"


def test_parse_subject_reads_core_fields():
    subject = parse_subject(
        fixture("handbook_subject_32146.json"), "https://example.test/32146", WHEN
    )
    assert subject.code == "32146"
    assert subject.name == "Data Visualisation and Visual Analytics"
    assert subject.credit_points == 6
    assert "<" not in subject.description
    assert subject.source_url == "https://example.test/32146"


def test_parse_subject_derives_sessions_from_offerings():
    subject = parse_subject(fixture("handbook_subject_32146.json"), "https://example.test", WHEN)
    assert subject.sessions == ["Autumn", "Spring"]


def test_parse_course_reads_structure():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    assert course.code == "C04296"
    assert course.credit_points == 96
    titles = {block.title for block in course.blocks}
    assert {"Core", "Majors", "Sub-Majors"} <= titles


def test_parse_course_keeps_core_subjects_with_credit_points():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    core = next(block for block in course.blocks if block.title == "Core")
    assert core.credit_points == 18
    assert {item.code for item in core.items} == {"32541", "43030", "32144"}
    assert all(item.credit_points == 6 for item in core.items)
    assert all(item.item_type == "subject" for item in core.items)


def test_parse_course_preserves_nesting():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    project = next(block for block in course.blocks if "Project" in block.title)
    assert project.children, "IT Project and Electives should have nested blocks"


def test_referenced_codes_filters_by_item_type():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    majors = referenced_codes(course, "major")
    assert "MAJ02093" in majors
    assert all(code.startswith("MAJ") for code in majors)


def test_referenced_codes_walks_nested_children_and_deduplicates():
    def item(code):
        return BlockItem(code=code, name=f"Subject {code}", item_type="subject")

    course = Course(
        code="C00000",
        title="Test",
        blocks=[
            CourseBlock(
                title="Outer",
                items=[item("11111")],
                children=[CourseBlock(title="Inner", items=[item("22222"), item("11111")])],
            )
        ],
        source_url="https://example.test",
        fetched_at="2026-08-26T00:00:00",
    )
    assert referenced_codes(course, "subject") == ["11111", "22222"]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("6", 6),
        (6, 6),
        ("6.0", 6),
        (6.0, 6),
        (None, None),
        ("", None),
        ("  6  ", 6),
    ],
)
def test_as_int_coerces_or_reports_absence(value, expected):
    assert _as_int(value) == expected


@pytest.mark.parametrize("value", [6.5, "abc", "6.5"])
def test_as_int_raises_on_unparseable_present_value(value):
    with pytest.raises(ValueError):
        _as_int(value)


def test_parse_subject_raises_when_credit_points_absent():
    page_content = fixture("handbook_subject_32146.json")
    page_content = dict(page_content)
    page_content["credit_points"] = None
    with pytest.raises(ValueError, match="credit points"):
        parse_subject(page_content, "https://example.test", WHEN)
