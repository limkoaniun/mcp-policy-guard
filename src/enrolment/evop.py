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

# The requisite table is terminated by whichever of these appears first. A
# single literal terminator is not safe: `evop_42890.html` already lacks
# "Note: See also", so a page that drops it would fold the entire page footer
# into the last item's `text` and still report it as a resolvable item with a
# corrupt descriptor.
END_MARKERS = (
    "Note: See also",
    "The requisites for this subject",
    "© Copyright UTS",
)

# No published requisite row comes close to this length. Anything longer means
# the table's end boundary was missed, so the row is flagged `unparsed` rather
# than trusted: `_is_unresolvable` then keeps it out of every satisfied answer.
MAX_ITEM_TEXT = 400

# A genuine access-conditions page proves itself one of two ways: it names the
# subject in a "<code> <title>" header, or it says outright that the subject
# has no restrictions. Without either signal the page is something else — a
# login redirect, a ColdFusion error, an empty body — and inferring "no
# requisites" from it would publish a confidently empty dataset.
NO_RESTRICTIONS_MARKER = "There are no access restrictions enforced for this subject"
HEADER_RE = re.compile(r"^(\d{5,6})\s+\S")

TYPE_WORDS = {
    "Academic requisite": "academic",
    "Admission requisite": "admission",
    "Other requisite": "other",
}
HEADER_WORDS = {"Item", "Type", "Details"}


class EvopPageError(ValueError):
    """Raised when a page is not a recognisable eVOP access-conditions page."""


def flatten(html: str) -> list[str]:
    """Reduce the page to a list of non-empty text lines."""
    text = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html_module.unescape(text)
    return [line.strip() for line in text.split("\n") if line.strip()]


def classify_item(label: str, item_type: str, text: str) -> RequisiteItem:
    """Turn one table row's payload into a typed item, never discarding `text`.

    Order matters twice over:

    - UTS publishes area-of-study requisites with the same "Admission into …"
      prefix as true admission requisites ("Admission into MAJ02902 36cp
      Information Technology Major MBA"). The descriptor is therefore tested
      for an area-of-study code *before* the row is accepted as an admission
      requisite; otherwise a student who genuinely holds the major is scored
      as failing a requisite they were never asked for. The admission
      descriptor is kept on the item either way, for provenance.
    - The subject-code match runs before the credit-point search, so a subject
      whose title happened to contain "at least N credit points" is still read
      as the subject it is.
    """
    if len(text) > MAX_ITEM_TEXT:
        # Almost certainly the requisite table's end boundary was missed and
        # page furniture has been folded into this row. Never trust it.
        return RequisiteItem(label=label, kind="unparsed", type=item_type, text=text)

    admission = ADMISSION_RE.match(text)
    if admission:
        descriptor = admission.group(1).strip()
        aos = AOS_RE.match(descriptor)
        if aos:
            return RequisiteItem(
                label=label,
                kind="aos",
                type=item_type,
                text=text,
                aos_code=aos.group(1),
                admission_descriptor=descriptor,
            )
        course = COURSE_CODE_RE.search(descriptor)
        return RequisiteItem(
            label=label,
            kind="admission",
            type=item_type,
            text=text,
            course=course.group(1) if course else None,
            admission_descriptor=descriptor,
        )

    subject = SUBJECT_RE.match(text)
    if subject:
        return RequisiteItem(
            label=label, kind="subject", type=item_type, text=text, subject=subject.group(1)
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


def _check_is_access_conditions_page(lines: list[str], code: str, table_start: int) -> None:
    """Fail unless the page positively identifies itself as this subject's page.

    Absence of rule lines is not evidence that a subject has no requisites: a
    login page, an application error page and an empty body all look exactly
    the same to a parser that only looks for the rule prefixes. The captured
    pages carry a `Logout` link, so `search.cfm` may well be session-scoped —
    an unauthenticated run must abort, not emit an empty dataset and exit 0.
    """
    # Restrict the header scan to what precedes the tables: a requisite row
    # such as "32144 Technology Research Preparation" is shaped like a header.
    headers = [
        match.group(1)
        for line in lines[:table_start]
        if (match := HEADER_RE.match(line))
    ]
    # The header is checked before the no-restrictions notice, so a page that
    # names a different subject is rejected either way round.
    if headers and code not in headers:
        raise EvopPageError(
            f"eVOP page fetched for subject {code} identifies itself as subject "
            f"{headers[0]}; refusing to attribute its content to {code}."
        )
    if headers:
        return
    if any(line.startswith(NO_RESTRICTIONS_MARKER) for line in lines):
        return
    raise EvopPageError(
        f"eVOP page for subject {code} is not an access-conditions page: it carries "
        f"neither a '<code> <title>' header nor the no-restrictions notice. "
        f"Refusing to read this as 'no requisites published'."
    )


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
    end = next(
        (i for i, line in enumerate(lines) if line.startswith(END_MARKERS)), len(lines)
    )
    heading_idx = next((i for i, line in enumerate(lines) if line == REQUISITE_HEADING), None)

    table_start = min(
        (i for i in (anti_rule_idx, rule_idx, heading_idx) if i is not None), default=len(lines)
    )
    _check_is_access_conditions_page(lines, code, table_start)

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
