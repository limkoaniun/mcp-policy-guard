"""Build the enrolment dataset from UTS sources.

Run once, commit the result. Experiments read `data/*.json` and never touch
the network.

    python -m enrolment.build --scope thin
    python -m enrolment.build --scope full --fail-on-unparsed

The thin scope is the core subjects plus the Cybersecurity major, and exists
to prove the pipeline before widening to the whole course.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .evop import parse_evop
from .handbook import extract_page_content, parse_course, parse_subject, referenced_codes
from .models import Course, Subject, SubjectRequisites

YEAR = "2026"
COURSE_CODE = "C04296"
CORE_CODES = ["32541", "43030", "32144"]
THIN_MAJOR = "MAJ02093"

HANDBOOK = "https://coursehandbook.uts.edu.au"
EVOP = "https://studentforms.uts.edu.au/evop/access/search.cfm"


def handbook_course_url(code: str = COURSE_CODE) -> str:
    return f"{HANDBOOK}/course/{YEAR}/{code.lower()}"


def handbook_aos_url(code: str) -> str:
    return f"{HANDBOOK}/aos/{YEAR}/{code}"


def handbook_subject_url(code: str) -> str:
    return f"{HANDBOOK}/subject/{YEAR}/{code}"


def evop_url(code: str) -> str:
    return f"{EVOP}?subjectcode={code}"


@dataclass
class CoverageReport:
    subjects: int
    with_requisite_rules: int
    without_requisite_rules: int
    unparsed_items: list[tuple[str, str, str]] = field(default_factory=list)
    external_subject_references: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.unparsed_items

    def render(self) -> str:
        lines = [
            f"subjects:                    {self.subjects}",
            f"with requisite rules:        {self.with_requisite_rules}",
            f"without requisite rules:     {self.without_requisite_rules}",
            f"unparsed requisite items:    {len(self.unparsed_items)}",
            f"references outside C04296:   {len(self.external_subject_references)}",
        ]
        for code, label, text in self.unparsed_items:
            lines.append(f"  UNPARSED {code} item {label}: {text}")
        return "\n".join(lines)


def coverage_report(
    catalogue: dict[str, Subject], requisites: dict[str, SubjectRequisites]
) -> CoverageReport:
    unparsed: list[tuple[str, str, str]] = []
    external: list[str] = []
    with_rules = 0
    for code, record in requisites.items():
        if record.requisite_rule:
            with_rules += 1
        for table in (record.requisite_items, record.antirequisite_items):
            for label, item in table.items():
                if item.kind == "unparsed":
                    unparsed.append((code, label, item.text))
                if (
                    item.kind == "subject"
                    and item.subject
                    and item.subject not in catalogue
                    and item.subject not in external
                ):
                    external.append(item.subject)
    return CoverageReport(
        subjects=len(catalogue),
        with_requisite_rules=with_rules,
        without_requisite_rules=len(requisites) - with_rules,
        unparsed_items=unparsed,
        external_subject_references=external,
    )


def _subject_codes(course: Course) -> list[str]:
    return referenced_codes(course, "subject")


def subject_codes_for_scope(
    course: Course, aos_courses: dict[str, Course], scope: str
) -> list[str]:
    codes = list(CORE_CODES) if scope == "thin" else _subject_codes(course)
    for aos in aos_courses.values():
        for code in _subject_codes(aos):
            if code not in codes:
                codes.append(code)
    return codes


def _load_course(fetcher, code: str, url: str, name: str) -> Course:
    result = fetcher.get(url, name)
    return parse_course(
        extract_page_content(result.body), result.url, result.fetched_at, code=code
    )


def build(
    data_dir, fetcher, scope: str = "thin", codes: list[str] | None = None
) -> CoverageReport:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    course = _load_course(
        fetcher, COURSE_CODE, handbook_course_url(), f"handbook_course_{COURSE_CODE.lower()}.html"
    )

    if codes is None:
        wanted = (
            [THIN_MAJOR]
            if scope == "thin"
            else referenced_codes(course, "major")
            + referenced_codes(course, "sub_major")
            + referenced_codes(course, "stream")
        )
        aos_courses = {
            aos: _load_course(fetcher, aos, handbook_aos_url(aos), f"handbook_aos_{aos}.html")
            for aos in wanted
        }
        codes = subject_codes_for_scope(course, aos_courses, scope)

    catalogue: dict[str, Subject] = {}
    requisites: dict[str, SubjectRequisites] = {}
    for code in codes:
        page = fetcher.get(handbook_subject_url(code), f"handbook_subject_{code}.html")
        catalogue[code] = parse_subject(
            extract_page_content(page.body), page.url, page.fetched_at
        )
        access = fetcher.get(evop_url(code), f"evop_{code}.html")
        requisites[code] = parse_evop(access.body, code, access.url, access.fetched_at)

    _write(data_dir / "course.json", course.model_dump())
    _write(data_dir / "catalogue.json", {c: s.model_dump() for c, s in catalogue.items()})
    _write(data_dir / "requisites.json", {c: r.model_dump() for c, r in requisites.items()})

    return coverage_report(catalogue, requisites)


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the enrolment dataset from UTS sources.")
    parser.add_argument("--scope", choices=["thin", "full"], default="thin")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument(
        "--fail-on-unparsed",
        action="store_true",
        help="exit non-zero if any requisite item could not be interpreted",
    )
    args = parser.parse_args(argv)

    from .fetch import Fetcher

    fetcher = Fetcher(cache_dir=Path(args.data_dir) / "raw", delay=args.delay)
    report = build(args.data_dir, fetcher, scope=args.scope)
    print(report.render())
    return 1 if args.fail_on_unparsed and not report.clean else 0


if __name__ == "__main__":
    raise SystemExit(main())
