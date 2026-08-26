import json
from pathlib import Path

import pytest

from enrolment.handbook import (
    extract_page_content,
    parse_course,
    parse_subject,
    referenced_codes,
    strip_html,
)

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
