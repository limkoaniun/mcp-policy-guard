"""Extractors for the UTS course handbook.

The handbook is a Next.js application: every page embeds its data as JSON in
a `__NEXT_DATA__` script tag, so extraction is a JSON traversal rather than
HTML scraping. Course structure lives under
`curriculumStructure.container[].relationship[]`, where each relationship
carries an item code, credit points, type and a `/aos/<year>/<CODE>` URL for
majors, sub-majors and elective streams.

Note that the handbook's own requisite fields are empty for every subject —
requisites come from `enrolment.evop` instead.
"""

from __future__ import annotations

import html as html_module
import json
import re

from .models import BlockItem, Course, CourseBlock, Subject

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


def extract_page_content(html: str) -> dict:
    match = NEXT_DATA_RE.search(html)
    if match is None:
        raise ValueError("page carries no __NEXT_DATA__ payload")
    return json.loads(match.group(1))["props"]["pageProps"]["pageContent"]


def strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()


def _as_int(value) -> int | None:
    """Coerce a handbook numeric field to an int.

    Genuinely absent values (None, blank string) become None. Anything present
    but not a whole number raises: a silently wrong credit-point value would
    corrupt the credit-point rules this dataset exists to enforce, so it must
    fail loudly rather than degrade to None or 0.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"handbook numeric field is not numeric: {value!r}") from None
    if number != int(number):
        raise ValueError(f"handbook numeric field is not a whole number: {value!r}")
    return int(number)


def parse_subject(page_content: dict, source_url: str, fetched_at: str) -> Subject:
    sessions = sorted(
        {
            (offering.get("teaching_period") or "").removesuffix(" Session").strip()
            for offering in page_content.get("offering") or []
            if offering.get("teaching_period")
        }
    )
    # code and title are indexed directly, not .get(): a subject page missing
    # either is unusable, so fail fast rather than publish a blank record.
    credit_points = _as_int(page_content.get("credit_points"))
    if credit_points is None:
        raise ValueError(f"subject {page_content['code']} publishes no credit points")
    return Subject(
        code=page_content["code"],
        name=page_content["title"],
        credit_points=credit_points,
        description=strip_html(page_content.get("description") or ""),
        sessions=[session for session in sessions if session],
        source_url=source_url,
        fetched_at=fetched_at,
    )


def _block(container: dict) -> CourseBlock:
    items = []
    for relationship in container.get("relationship") or []:
        code = relationship.get("academic_item_code")
        if not code:
            continue
        items.append(
            BlockItem(
                code=code,
                name=relationship.get("academic_item_name") or "",
                credit_points=_as_int(relationship.get("academic_item_credit_points")),
                item_type=(relationship.get("academic_item_type") or {}).get("value") or "unknown",
                url=relationship.get("academic_item_url") or None,
                connector=(relationship.get("parent_connector") or {}).get("value") or None,
            )
        )
    return CourseBlock(
        title=container.get("title") or container.get("name") or "",
        description=strip_html(container.get("description") or ""),
        credit_points=_as_int(container.get("credit_points")),
        items=items,
        children=[_block(child) for child in container.get("container") or []],
    )


def parse_course(
    page_content: dict, source_url: str, fetched_at: str, code: str | None = None
) -> Course:
    """Parse a course or area-of-study page.

    `/aos/<year>/<CODE>` pages publish majors, sub-majors and elective streams
    using the same curriculum-structure shape as a course page, but without a
    `code` field — hence the override.
    """
    structure = page_content.get("curriculumStructure") or {}
    return Course(
        code=code or page_content.get("code") or "",
        title=page_content.get("title") or "",
        credit_points=_as_int(page_content.get("credit_points")),
        blocks=[_block(container) for container in structure.get("container") or []],
        source_url=source_url,
        fetched_at=fetched_at,
    )


def referenced_codes(course: Course, item_type: str) -> list[str]:
    """Every code of the given item type anywhere in the course tree, deduplicated."""
    found: list[str] = []

    def walk(block: CourseBlock) -> None:
        for item in block.items:
            if item.item_type == item_type and item.code not in found:
                found.append(item.code)
        for child in block.children:
            walk(child)

    for block in course.blocks:
        walk(block)
    return found
