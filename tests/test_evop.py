from pathlib import Path

import pytest

from enrolment.evop import classify_item, parse_evop

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://studentforms.uts.edu.au/evop/access/search.cfm?subjectcode="
WHEN = "2026-08-26T00:00:00"


def load(code: str):
    html = (FIXTURES / f"evop_{code}.html").read_text(encoding="utf-8", errors="replace")
    return parse_evop(html, code, URL + code, WHEN)


def test_parses_simple_rule_and_both_tables():
    req = load("31251")
    assert req.requisite_rule == "(1)"
    assert req.antirequisite_rule == "(1 AND 2)"
    assert req.requisite_items["1"].subject == "48024"
    assert req.requisite_items["1"].kind == "subject"
    assert req.requisite_items["1"].type == "academic"
    assert req.antirequisite_items["1"].subject == "31473"
    assert req.antirequisite_items["2"].subject == "32510"


def test_parses_deeply_nested_rule():
    req = load("32146")
    assert req.requisite_rule == "((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))"
    assert req.antirequisite_rule == "(1 AND 2 AND 3)"
    assert set(req.requisite_items) == {"1", "2", "2a", "2b", "3", "4", "5", "6"}
    assert req.requisite_items["1"].subject == "31250"
    assert req.requisite_items["2a"].subject == "43031"


def test_distinguishes_academic_from_admission_items():
    req = load("32933")
    assert req.requisite_items["1"].kind == "subject"
    assert req.requisite_items["1"].subject == "32144"
    assert req.requisite_items["2"].kind == "admission"
    assert req.requisite_items["2"].type == "admission"
    assert "FEIT" in req.requisite_items["2"].admission_descriptor


def test_subject_without_requisites_has_no_rules():
    req = load("42890")
    assert req.requisite_rule is None
    assert req.antirequisite_rule is None
    assert req.requisite_items == {}


def test_provenance_is_recorded():
    req = load("31251")
    assert req.source_url.endswith("31251")
    assert req.fetched_at == WHEN


@pytest.mark.parametrize(
    "text,kind,attr,value",
    [
        ("48024 Programming 2", "subject", "subject", "48024"),
        ("MAJ02902 36cp Information Technology Major MBA", "aos", "aos_code", "MAJ02902"),
        (
            "Admission into C04296 Master of Information Technology (Extension)",
            "admission",
            "course",
            "C04296",
        ),
        (
            "Must have completed at least 48 credit points in 48.0000000000 Credit Points",
            "credit_points",
            "credit_points",
            48,
        ),
    ],
)
def test_classify_item_recognises_each_payload_kind(text, kind, attr, value):
    item = classify_item("1", "academic", text)
    assert item.kind == kind
    assert getattr(item, attr) == value


def test_unrecognised_payload_is_kept_as_unparsed():
    item = classify_item("1", "other", "some wording we have never seen")
    assert item.kind == "unparsed"
    assert item.text == "some wording we have never seen"
