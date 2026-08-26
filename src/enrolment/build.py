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
from .rules import RuleSyntaxError
from .rules import parse as parse_rule

YEAR = "2026"
COURSE_CODE = "C04296"
CORE_CODES = ["32541", "43030", "32144"]
THIN_MAJOR = "MAJ02093"
SCOPES = ("thin", "full")

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
    """What the build actually managed to read, and what it could not.

    This is the gate that decides whether a build is usable, so every count it
    prints has to be one a failure cannot hide behind. "without requisite
    rules" was such a hiding place: subjects whose fetch or parse produced
    nothing were indistinguishable from subjects that genuinely publish only
    an anti-requisite rule, so it is split into its two meanings here.
    """

    subjects: int
    with_requisite_rules: int
    antirequisite_only: int = 0
    no_rules_at_all: int = 0
    unparsed_items: list[tuple[str, str, str]] = field(default_factory=list)
    unparsed_rules: list[tuple[str, str, str, str]] = field(default_factory=list)
    external_subject_references: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        if self.unparsed_items or self.unparsed_rules:
            return False
        # A build that produced subjects but not one requisite rule is a
        # failure signal, not a finding: it is what an unauthenticated or
        # otherwise broken eVOP fetch looks like from the outside.
        return not (self.subjects and self.with_requisite_rules == 0)

    def render(self) -> str:
        lines = [
            f"subjects:                    {self.subjects}",
            f"with requisite rules:        {self.with_requisite_rules}",
            f"anti-requisite only:         {self.antirequisite_only}",
            f"no rules at all:             {self.no_rules_at_all}",
            f"unparsed requisite items:    {len(self.unparsed_items)}",
            f"unparsed rule expressions:   {len(self.unparsed_rules)}",
            f"references outside C04296:   {len(self.external_subject_references)}",
        ]
        for code, label, text in self.unparsed_items:
            lines.append(f"  UNPARSED {code} item {label}: {text}")
        for code, which, rule, message in self.unparsed_rules:
            lines.append(f"  UNPARSED RULE {code} {which}: {rule!r} -- {message}")
        if self.subjects and self.with_requisite_rules == 0:
            lines.append(
                "  NO REQUISITE RULES WERE FOUND FOR ANY SUBJECT: "
                "treat this build as failed, not as a course without requisites."
            )
        return "\n".join(lines)


def coverage_report(
    catalogue: dict[str, Subject], requisites: dict[str, SubjectRequisites]
) -> CoverageReport:
    unparsed: list[tuple[str, str, str]] = []
    unparsed_rules: list[tuple[str, str, str, str]] = []
    external: list[str] = []
    with_rules = 0
    anti_only = 0
    no_rules = 0
    for code, record in requisites.items():
        if record.requisite_rule:
            with_rules += 1
        elif record.antirequisite_rule:
            anti_only += 1
        else:
            no_rules += 1
        # Parse every published expression here, at the gate. Leaving it to
        # `generate_scenarios` meant a RuleSyntaxError surfaced as a crash
        # after the whole build had completed, with no partial output and no
        # itemised account of what was wrong.
        for which, rule in (
            ("requisite", record.requisite_rule),
            ("antirequisite", record.antirequisite_rule),
        ):
            if not rule:
                continue
            try:
                parse_rule(rule)
            except RuleSyntaxError as exc:
                unparsed_rules.append((code, which, rule, str(exc)))
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
        antirequisite_only=anti_only,
        no_rules_at_all=no_rules,
        unparsed_items=unparsed,
        unparsed_rules=unparsed_rules,
        external_subject_references=external,
    )


def _subject_codes(course: Course) -> list[str]:
    return referenced_codes(course, "subject")


def _check_scope(scope: str) -> None:
    """Reject an unknown scope instead of quietly widening to the full course.

    Any string other than "thin" fell through to full-course behaviour, so a
    typo produced a much larger fetch than asked for and reported nothing
    unusual about it.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}: expected one of {SCOPES}")


def subject_codes_for_scope(
    course: Course, aos_courses: dict[str, Course], scope: str
) -> list[str]:
    _check_scope(scope)
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
    _check_scope(scope)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    course = _load_course(
        fetcher, COURSE_CODE, handbook_course_url(), f"handbook_course_{COURSE_CODE.lower()}.html"
    )

    aos_courses: dict[str, Course] = {}
    derived_codes = codes is None
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

    # The majors and sub-majors were fetched to derive subject codes; keep
    # them, so the set of real area-of-study codes is on record rather than
    # discarded. `students.py` needs them to give a cohort real variance.
    # Only written when this run actually derived them: a targeted rebuild
    # with an explicit code list must not replace a good aos.json with {}.
    if derived_codes:
        _write(data_dir / "aos.json", {c: a.model_dump() for c, a in aos_courses.items()})
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
    if report.subjects and report.with_requisite_rules == 0:
        # Unconditional, not gated behind --fail-on-unparsed: a dataset with
        # no requisite rules at all is not a finding to review later, it is a
        # build that did not work.
        return 1
    return 1 if args.fail_on_unparsed and not report.clean else 0


if __name__ == "__main__":
    raise SystemExit(main())
