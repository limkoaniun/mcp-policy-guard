"""Parser for UTS eVOP access-conditions pages.

`studentforms.uts.edu.au/evop/access/search.cfm?subjectcode=<code>` is where
UTS publishes requisites; the course handbook's own requisite fields are
empty. Each page carries up to two tables — anti-requisites and requisites —
each with a boolean rule expression over numbered items.

The page is server-rendered HTML with no stable class names, so parsing works
on the flattened text: every tag becomes a line break, and records are
delimited by lines that are bare item labels.
"""

from __future__ import annotations

import html as html_module
import re

from .models import RequisiteItem, SubjectRequisites

LABEL_RE = re.compile(r"^\d+[a-z]*$")
SUBJECT_RE = re.compile(r"^(\d{5,6})\s+(.+)$")
AOS_RE = re.compile(r"^((?:MAJ|SMJ|CBK|STM)\d+)\s+(.+)$")
ADMISSION_RE = re.compile(r"^Admission\s+(?:into\s+)?(.*)$", re.IGNORECASE)
COURSE_CODE_RE = re.compile(r"\b(C\d{5})\b")
CREDIT_POINTS_RE = re.compile(r"at least\s+(\d+)\s+credit points", re.IGNORECASE)

ANTI_RULE_PREFIX = "Anti-requisite rule:"
RULE_PREFIX = "Requisite rule:"
REQUISITE_HEADING = "Requisite(s)"
END_MARKER = "Note: See also"

TYPE_WORDS = {
    "Academic requisite": "academic",
    "Admission requisite": "admission",
    "Other requisite": "other",
}
HEADER_WORDS = {"Item", "Type", "Details"}


def flatten(html: str) -> list[str]:
    """Reduce the page to a list of non-empty text lines."""
    text = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html_module.unescape(text)
    return [line.strip() for line in text.split("\n") if line.strip()]


def classify_item(label: str, item_type: str, text: str) -> RequisiteItem:
    """Turn one table row's payload into a typed item, never discarding `text`."""
    admission = ADMISSION_RE.match(text)
    if admission:
        descriptor = admission.group(1).strip()
        course = COURSE_CODE_RE.search(descriptor)
        return RequisiteItem(
            label=label,
            kind="admission",
            type=item_type,
            text=text,
            course=course.group(1) if course else None,
            admission_descriptor=descriptor,
        )

    credit_points = CREDIT_POINTS_RE.search(text)
    if credit_points:
        return RequisiteItem(
            label=label,
            kind="credit_points",
            type=item_type,
            text=text,
            credit_points=int(credit_points.group(1)),
        )

    subject = SUBJECT_RE.match(text)
    if subject:
        return RequisiteItem(
            label=label, kind="subject", type=item_type, text=text, subject=subject.group(1)
        )

    aos = AOS_RE.match(text)
    if aos:
        return RequisiteItem(
            label=label, kind="aos", type=item_type, text=text, aos_code=aos.group(1)
        )

    return RequisiteItem(label=label, kind="unparsed", type=item_type, text=text)


def _parse_table(lines: list[str]) -> dict[str, RequisiteItem]:
    collected: dict[str, dict] = {}
    current: str | None = None
    for line in lines:
        if line in HEADER_WORDS:
            continue
        if LABEL_RE.match(line):
            current = line
            collected[current] = {"type": "unknown", "text": []}
            continue
        if current is None:
            continue
        record = collected[current]
        if not record["text"] and line in TYPE_WORDS:
            record["type"] = TYPE_WORDS[line]
            continue
        record["text"].append(line)
    return {
        label: classify_item(label, record["type"], " ".join(record["text"]).strip())
        for label, record in collected.items()
    }


def parse_evop(html: str, code: str, source_url: str, fetched_at: str) -> SubjectRequisites:
    lines = flatten(html)

    anti_rule_idx = next(
        (i for i, line in enumerate(lines) if line.startswith(ANTI_RULE_PREFIX)), None
    )
    # No need to exclude ANTI_RULE_PREFIX matches here: the two prefixes differ at
    # the first character ("A" vs "R"), so a line starting with RULE_PREFIX can
    # never also start with ANTI_RULE_PREFIX.
    rule_idx = next(
        (i for i, line in enumerate(lines) if line.startswith(RULE_PREFIX)), None
    )
    end = next((i for i, line in enumerate(lines) if line.startswith(END_MARKER)), len(lines))
    heading_idx = next((i for i, line in enumerate(lines) if line == REQUISITE_HEADING), None)

    result = SubjectRequisites(code=code, source_url=source_url, fetched_at=fetched_at)

    if anti_rule_idx is not None:
        result.antirequisite_rule = lines[anti_rule_idx][len(ANTI_RULE_PREFIX) :].strip()
        anti_end = (
            heading_idx if heading_idx is not None and heading_idx > anti_rule_idx else end
        )
        result.antirequisite_items = _parse_table(lines[anti_rule_idx + 1 : anti_end])

    if rule_idx is not None:
        result.requisite_rule = lines[rule_idx][len(RULE_PREFIX) :].strip()
        result.requisite_items = _parse_table(lines[rule_idx + 1 : end])

    return result
