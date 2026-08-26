import json
from pathlib import Path

from enrolment.build import (
    CoverageReport,
    build,
    coverage_report,
    evop_url,
    handbook_subject_url,
    subject_codes_for_scope,
)
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
        without_requisite_rules=1,
        unparsed_items=[],
        external_subject_references=["48024"],
    )
    assert report.clean is True


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
