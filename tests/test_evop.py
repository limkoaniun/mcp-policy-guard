from pathlib import Path

import pytest

from enrolment.evop import MAX_ITEM_TEXT, EvopPageError, classify_item, parse_evop

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


def test_area_of_study_requisites_are_not_read_as_admission_requisites():
    """UTS publishes aos requisites with the same "Admission into ..." prefix.

    Reading 2e/2f as admission requisites makes a student who genuinely holds
    the major fail a requisite they were never asked for, with a confident
    explanation naming the wrong rule.
    """
    req = load("32933")
    assert req.requisite_items["2e"].kind == "aos"
    assert req.requisite_items["2e"].aos_code == "MAJ02902"
    assert req.requisite_items["2f"].kind == "aos"
    assert req.requisite_items["2f"].aos_code == "SMJ02038"
    # Genuine admission requisites on the same page are untouched.
    assert req.requisite_items["2"].kind == "admission"


def test_area_of_study_item_keeps_its_admission_provenance():
    req = load("32933")
    item = req.requisite_items["2e"]
    assert item.admission_descriptor == "MAJ02902 36cp Information Technology Major MBA"
    assert item.text == "Admission into MAJ02902 36cp Information Technology Major MBA"


def test_subject_code_wins_over_a_credit_point_phrase_in_the_title():
    item = classify_item("1", "academic", "32144 Reading at least 6 credit points of theory")
    assert item.kind == "subject"
    assert item.subject == "32144"


def test_no_requisite_page_is_accepted_only_on_its_positive_marker():
    req = load("42890")
    assert req.requisite_rule is None


@pytest.mark.parametrize(
    "name,html",
    [
        (
            "login",
            (
                "<html><head><title>UTS: Login</title></head><body>"
                "<h1>Please log in</h1><form><input name='user'></form></body></html>"
            ),
        ),
        (
            "coldfusion error",
            (
                "<html><body><h1>Error Occurred While Processing Request</h1>"
                "<p>Element SUBJECTCODE is undefined in FORM.</p></body></html>"
            ),
        ),
        ("empty body", "<html><body></body></html>"),
        ("nothing at all", ""),
    ],
)
def test_unrecognised_page_raises_instead_of_reporting_no_requisites(name, html):
    with pytest.raises(EvopPageError) as excinfo:
        parse_evop(html, "32933", "https://example.test", WHEN)
    assert "32933" in str(excinfo.value)


def test_page_for_a_different_subject_is_rejected():
    html = (FIXTURES / "evop_31251.html").read_text(encoding="utf-8")
    with pytest.raises(EvopPageError) as excinfo:
        parse_evop(html, "32933", "https://example.test", WHEN)
    assert "31251" in str(excinfo.value)


def test_requisite_table_ends_at_the_footer_even_without_the_note_marker():
    """`evop_42890.html` already lacks "Note: See also", so this is not academic.

    Without a second terminator the page footer is folded into the last item's
    text, which then still reports as a resolvable admission requisite with a
    corrupt descriptor -- and that text is printed verbatim into the
    agent-facing deny message.
    """
    html = (FIXTURES / "evop_32933.html").read_text(encoding="utf-8")
    start = html.index("<p>\n\n\tNote: See also")
    stripped = html[:start] + html[html.index("<p>The requisites for this subject") :]
    assert "Note: See also" not in stripped
    req = parse_evop(stripped, "32933", "https://example.test", WHEN)
    last = req.requisite_items["2f"]
    assert last.text == "Admission into SMJ02038 Information Technology Sub-major MBA"
    assert last.kind == "aos"
    assert set(req.requisite_items) == {"1", "2", "2a", "2b", "2c", "2d", "2e", "2f"}


def test_absurdly_long_item_text_is_flagged_unparsed_not_trusted():
    text = "Admission into " + ("Master's Coursework courses owned by FEIT " * 20)
    assert len(text) > MAX_ITEM_TEXT
    item = classify_item("2", "admission", text)
    assert item.kind == "unparsed"
    assert item.text == text


def test_item_with_blank_details_is_kept_as_unparsed():
    """An &nbsp;-only details cell must not make a requisite disappear."""
    html = (
        "<p>32999 Test Subject</p>"
        "<p>Requisite rule: (1 OR 2)</p>"
        "<table>"
        "<tr><td>Item</td><td>Type</td><td>Details</td></tr>"
        "<tr><td>1</td><td>Academic requisite</td><td>48024 Programming 2</td></tr>"
        "<tr><td>2</td><td>Academic requisite</td><td>&nbsp;</td></tr>"
        "</table>"
        "<p>Note: See also the information on understanding subjects.</p>"
    )
    req = parse_evop(html, "32999", "https://example.test", "2026-08-26T00:00:00")
    assert set(req.requisite_items) == {"1", "2"}
    assert req.requisite_items["2"].kind == "unparsed"


def test_corequisite_rows_are_parsed_as_subjects():
    """UTS publishes a third type, 'Academic corequisite', found by the first real build."""
    req = load("32548")
    assert req.requisite_rule == "((1 OR 1a))"
    assert req.requisite_items["1"].kind == "subject"
    assert req.requisite_items["1"].subject == "32524"
    assert req.requisite_items["1a"].subject == "49202"


def test_corequisite_rows_are_flagged_as_corequisites():
    req = load("32548")
    assert req.requisite_items["1"].corequisite is True
    assert req.requisite_items["1"].type == "academic"


def test_ordinary_requisite_rows_are_not_flagged_as_corequisites():
    req = load("31251")
    assert req.requisite_items["1"].corequisite is False
