import json
from pathlib import Path

import pytest

from enrolment.build import (
    CoverageReport,
    build,
    coverage_report,
    evop_url,
    handbook_subject_url,
    subject_codes_for_scope,
)
from enrolment.evop import EvopPageError
from enrolment.models import (
    BlockItem,
    Course,
    CourseBlock,
    RequisiteItem,
    Subject,
    SubjectRequisites,
)

FIXTURES = Path(__file__).parent / "fixtures"
WHEN = "2026-08-26T00:00:00"


def wrap(page_content: dict) -> str:
    payload = json.dumps({"props": {"pageProps": {"pageContent": page_content}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


class FakeFetcher:
    """Serves committed fixtures by cache name; fails loudly on anything else."""

    def __init__(self):
        course = json.loads((FIXTURES / "handbook_course_c04296.json").read_text())
        subject = json.loads((FIXTURES / "handbook_subject_32146.json").read_text())
        self.bodies = {
            "handbook_course_c04296.html": wrap(course),
            "handbook_subject_32146.html": wrap(subject),
            "evop_32146.html": (FIXTURES / "evop_32146.html").read_text(
                encoding="utf-8", errors="replace"
            ),
        }
        self.requested: list[str] = []

    def get(self, url: str, name: str):
        from enrolment.fetch import FetchResult

        self.requested.append(name)
        if name not in self.bodies:
            raise AssertionError(f"unexpected fetch: {name}")
        return FetchResult(
            url=url, name=name, body=self.bodies[name], fetched_at=WHEN, from_cache=True
        )


def aos_page(title: str, *subject_codes: str) -> dict:
    return {
        "title": title,
        "credit_points": 24,
        "curriculumStructure": {
            "container": [
                {
                    "title": "Choose 24 credit points",
                    "relationship": [
                        {
                            "academic_item_code": code,
                            "academic_item_name": f"Subject {code}",
                            "academic_item_credit_points": 6,
                            "academic_item_type": {"value": "subject"},
                        }
                        for code in subject_codes
                    ],
                }
            ]
        },
    }


def subject_page(code: str) -> dict:
    return {"code": code, "title": f"Subject {code}", "credit_points": 6, "offering": []}


def evop_page(code: str) -> str:
    return (
        f"<p>{code} Subject {code}</p>"
        "<p>Requisite rule: (1)</p>"
        "<table>"
        "<tr><td>Item</td><td>Type</td><td>Details</td></tr>"
        "<tr><td>1</td><td>Academic requisite</td><td>48024 Programming 2</td></tr>"
        "</table>"
        "<p>Note: See also the information on understanding subjects.</p>"
    )


LOGIN_PAGE = "<html><body><h1>Please log in</h1><form><input name='u'></form></body></html>"


class SyntheticFetcher:
    """Serves a coherent, synthesised page for whatever the build asks for."""

    def __init__(self, aos_subjects: dict[str, list[str]], evop_body: str | None = None):
        course = json.loads((FIXTURES / "handbook_course_c04296.json").read_text())
        self.aos_subjects = aos_subjects
        self.evop_body = evop_body
        self.bodies = {"handbook_course_c04296.html": wrap(course)}
        for aos, codes in aos_subjects.items():
            self.bodies[f"handbook_aos_{aos}.html"] = wrap(aos_page(f"{aos} Major", *codes))
        self.requested: list[str] = []

    def get(self, url: str, name: str):
        from enrolment.fetch import FetchResult

        self.requested.append(name)
        if name in self.bodies:
            body = self.bodies[name]
        elif name.startswith("handbook_subject_"):
            body = wrap(subject_page(name.removeprefix("handbook_subject_").removesuffix(".html")))
        elif name.startswith("evop_"):
            body = self.evop_body or evop_page(
                name.removeprefix("evop_").removesuffix(".html")
            )
        else:
            raise AssertionError(f"unexpected fetch: {name}")
        return FetchResult(
            url=url, name=name, body=body, fetched_at=WHEN, from_cache=True
        )


def test_url_builders():
    assert handbook_subject_url("32146").endswith("/subject/2026/32146")
    assert evop_url("32146").endswith("subjectcode=32146")


def test_thin_scope_is_core_plus_the_named_major():
    course = Course(
        code="C04296",
        title="MIT (Extension)",
        blocks=[
            CourseBlock(
                title="Core",
                items=[
                    BlockItem(code="32541", name="Project Management", item_type="subject"),
                    BlockItem(code="43030", name="Professional Practice", item_type="subject"),
                    BlockItem(code="32144", name="Tech Research Prep", item_type="subject"),
                ],
            )
        ],
        source_url="https://example.test",
        fetched_at=WHEN,
    )
    cyber = Course(
        code="MAJ02093",
        title="Cybersecurity",
        blocks=[
            CourseBlock(
                title="Choose",
                items=[BlockItem(code="32309", name="Digital Forensics", item_type="subject")],
            )
        ],
        source_url="https://example.test",
        fetched_at=WHEN,
    )
    codes = subject_codes_for_scope(course, {"MAJ02093": cyber}, "thin")
    assert codes == ["32541", "43030", "32144", "32309"]


def test_coverage_report_counts_rules_and_unparsed_items():
    catalogue = {
        "32146": Subject(
            code="32146", name="X", credit_points=6, source_url="u", fetched_at=WHEN
        )
    }
    requisites = {
        "32146": SubjectRequisites(
            code="32146",
            requisite_rule="(1)",
            requisite_items={
                "1": RequisiteItem(label="1", kind="unparsed", text="mystery wording")
            },
            source_url="u",
            fetched_at=WHEN,
        )
    }
    report = coverage_report(catalogue, requisites)
    assert report.subjects == 1
    assert report.with_requisite_rules == 1
    assert report.unparsed_items == [("32146", "1", "mystery wording")]
    assert report.clean is False
    assert "mystery wording" in report.render()


def test_clean_report_has_no_unparsed_items():
    report = CoverageReport(
        subjects=3,
        with_requisite_rules=2,
        antirequisite_only=1,
        no_rules_at_all=0,
        unparsed_items=[],
        external_subject_references=["48024"],
    )
    assert report.clean is True


def test_a_build_with_no_requisite_rules_at_all_is_not_clean():
    """Zero rules across a whole build is what a broken eVOP fetch looks like.

    eVOP carries a Logout link, so search.cfm may be session-scoped; an
    unauthenticated run must not report a plausible, entirely empty dataset.
    """
    report = CoverageReport(subjects=12, with_requisite_rules=0, no_rules_at_all=12)
    assert report.clean is False
    assert report.produced_nothing is True
    assert "PRODUCED NO USABLE DATA" in report.render()


def test_a_build_with_no_subjects_at_all_is_not_clean():
    """An empty catalogue is the same failure one step earlier."""
    report = CoverageReport(subjects=0, with_requisite_rules=0)
    assert report.clean is False
    assert "PRODUCED NO USABLE DATA" in report.render()


def test_without_requisite_rules_is_split_into_its_two_meanings():
    catalogue = {
        code: Subject(code=code, name="X", credit_points=6, source_url="u", fetched_at=WHEN)
        for code in ("1", "2", "3")
    }
    requisites = {
        "1": SubjectRequisites(code="1", requisite_rule="(1)", source_url="u", fetched_at=WHEN),
        "2": SubjectRequisites(
            code="2", antirequisite_rule="(1)", source_url="u", fetched_at=WHEN
        ),
        "3": SubjectRequisites(code="3", source_url="u", fetched_at=WHEN),
    }
    report = coverage_report(catalogue, requisites)
    assert (report.with_requisite_rules, report.antirequisite_only, report.no_rules_at_all) == (
        1,
        1,
        1,
    )
    rendered = report.render()
    assert "anti-requisite only:         1" in rendered
    assert "no rules at all:             1" in rendered


def test_unparseable_rule_is_reported_by_the_gate_not_raised_later():
    """A RuleSyntaxError used to surface inside generate_scenarios instead.

    That is after the whole build has completed, with no partial output and no
    itemised account -- while the coverage report called it clean.
    """
    catalogue = {
        "32146": Subject(
            code="32146", name="X", credit_points=6, source_url="u", fetched_at=WHEN
        )
    }
    requisites = {
        "32146": SubjectRequisites(
            code="32146",
            requisite_rule="(1 AND 2 OR)",
            antirequisite_rule="(1 AND",
            source_url="u",
            fetched_at=WHEN,
        )
    }
    report = coverage_report(catalogue, requisites)
    assert report.clean is False
    assert [(code, which) for code, which, _, _ in report.unparsed_rules] == [
        ("32146", "requisite"),
        ("32146", "antirequisite"),
    ]
    rendered = report.render()
    assert "unparsed rule expressions:   2" in rendered
    assert "(1 AND 2 OR)" in rendered


def test_valid_rules_are_not_reported_as_unparsed():
    requisites = {
        "32146": SubjectRequisites(
            code="32146",
            requisite_rule="((1 AND (2 OR 2a)) OR (3))",
            antirequisite_rule="(1 AND 2)",
            source_url="u",
            fetched_at=WHEN,
        )
    }
    catalogue = {
        "32146": Subject(
            code="32146", name="X", credit_points=6, source_url="u", fetched_at=WHEN
        )
    }
    assert coverage_report(catalogue, requisites).unparsed_rules == []


@pytest.mark.parametrize("scope", ["Thin", "", "everything", "full-ish"])
def test_unknown_scope_is_rejected_rather_than_silently_widened(scope):
    course = Course(code="C04296", title="X", source_url="u", fetched_at=WHEN)
    with pytest.raises(ValueError, match="unknown scope"):
        subject_codes_for_scope(course, {}, scope)


def test_build_writes_all_three_files(tmp_path):
    fetcher = FakeFetcher()
    report = build(tmp_path, fetcher, scope="thin", codes=["32146"])

    catalogue = json.loads((tmp_path / "catalogue.json").read_text())
    requisites = json.loads((tmp_path / "requisites.json").read_text())
    course = json.loads((tmp_path / "course.json").read_text())

    assert catalogue["32146"]["name"] == "Data Visualisation and Visual Analytics"
    assert requisites["32146"]["requisite_rule"].startswith("((1 AND")
    assert course["code"] == "C04296"
    assert report.subjects == 1
    assert report.clean is True


def test_build_records_the_area_of_study_structures_it_fetched(tmp_path):
    """The aos pages were fetched to derive subject codes, then thrown away.

    Without them the set of real MAJ/SMJ codes is nowhere in the dataset, and
    the aos requisites this package now parses cannot be checked against it.
    """
    fetcher = SyntheticFetcher({"MAJ02093": ["32309", "32548"]})
    build(tmp_path, fetcher, scope="thin")

    aos = json.loads((tmp_path / "aos.json").read_text())
    assert set(aos) == {"MAJ02093"}
    assert aos["MAJ02093"]["code"] == "MAJ02093"
    assert [item["code"] for item in aos["MAJ02093"]["blocks"][0]["items"]] == [
        "32309",
        "32548",
    ]
    catalogue = json.loads((tmp_path / "catalogue.json").read_text())
    assert set(catalogue) == {"32541", "43030", "32144", "32309", "32548"}


def test_a_build_served_login_pages_aborts_instead_of_writing_an_empty_dataset(tmp_path):
    """End-to-end: the whole point of the eVOP page check.

    An unauthenticated run must not write catalogue.json and requisites.json
    full of subjects with no requisites and then exit 0.
    """
    fetcher = SyntheticFetcher({"MAJ02093": ["32309"]}, evop_body=LOGIN_PAGE)

    with pytest.raises(EvopPageError, match="32541"):
        build(tmp_path, fetcher, scope="thin")
    assert not (tmp_path / "requisites.json").exists()


def test_a_targeted_rebuild_does_not_blank_an_existing_aos_file(tmp_path):
    """Passing an explicit code list fetches no aos pages; {} would be a lie."""
    (tmp_path / "aos.json").write_text('{"MAJ02093": {}}', encoding="utf-8")
    build(tmp_path, FakeFetcher(), scope="thin", codes=["32146"])
    assert json.loads((tmp_path / "aos.json").read_text()) == {"MAJ02093": {}}
