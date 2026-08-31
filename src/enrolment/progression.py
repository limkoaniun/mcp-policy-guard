"""Study-plan progression: how much of a course a student has completed.

`enrolment.requisites` answers "may this student enrol in this subject".
This module answers the other half: does a *set* of completed subjects
satisfy the course, and what does changing major cost.

Two published facts make that harder than a lookup, and both are why this
module exists rather than a dict comprehension at a call site:

- Every major dual-lists subjects across its own blocks. Cybersecurity
  lists 42035, 42891, 42036 and 42037 in both Options and Advanced Options.
- A subject may only be counted once.

So deciding how much of a course is complete is a maximum assignment under
quota constraints, not a filter. Measured against the committed dataset, a
naive first-fit allocation reports the wrong credit total in 27.4% of cases.
See `context/2026-08-31-scenario-suite-design.md` §2.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from .models import Course, CourseBlock

BlockKind = Literal["core", "project", "major", "sub_major", "elective"]

# Tie-break weights. Where several assignments tie on total counted credit,
# the one filling the most constrained blocks wins. This is a stated reading,
# not a published rule: it is worth one session of study to a student
# changing major, so the report must name it. Spec §7.
PRIORITY: dict[BlockKind, int] = {
    "core": 40,
    "project": 30,
    "major": 20,
    "sub_major": 10,
    "elective": 0,
}


class Block(BaseModel):
    """One leaf selection block: a credit-point quota over eligible subjects."""

    path: str
    quota: int
    eligible: frozenset[str]
    kind: BlockKind


def _compute_core_or_project(container: CourseBlock) -> BlockKind:
    """Determine if a block is 'core' (complete-all) or 'project' (select-from).

    A complete-all block's quota equals the sum of its items' credit points.
    A select-from block's quota is less than that sum.
    If any item has None credit_points, treat it as 0 and force 'project'.
    """
    subject_items = [i for i in container.items if i.item_type == "subject"]
    if not subject_items:
        return "project"

    # If any item has None credit_points, force project
    if any(i.credit_points is None for i in subject_items):
        return "project"

    total_cp = sum(i.credit_points for i in subject_items)
    quota = container.credit_points or 0

    # If quota equals total, it's core; otherwise project
    return "core" if quota == total_cp else "project"


def _leaves(
    node: Course,
    prefix: str,
    kind: BlockKind | Callable[[CourseBlock], BlockKind],
) -> list[Block]:
    """Every block of `node` that directly lists subjects."""
    found: list[Block] = []

    def walk(container, path: str) -> None:
        name = f"{path}/{container.title}" if path else container.title
        codes = [i.code for i in container.items if i.item_type == "subject"]
        for child in container.children:
            walk(child, name)
        if codes:
            block_kind = kind(container) if callable(kind) else kind
            found.append(
                Block(
                    path=f"{prefix}{name}",
                    quota=container.credit_points or 0,
                    eligible=frozenset(codes),
                    kind=block_kind,
                )
            )

    for container in node.blocks:
        walk(container, "")
    return found


def expand_structure(
    course: Course,
    aos: dict[str, Course],
    major: str | None,
    sub_major: str | None,
) -> list[Block]:
    """Flatten a course into leaf blocks, given a chosen major and sub-major.

    An unchosen choice point contributes no blocks at all, rather than
    contributing an empty one: a student with no declared major has no major
    requirement to be short of, and inventing a 36cp hole would report a
    completion gap they do not have.
    """
    blocks: list[Block] = []
    for container in course.blocks:
        types = {i.item_type for i in container.items}
        if "major" in types:
            if major:
                blocks += _leaves(aos[major], f"Major:{aos[major].title}/", "major")
        elif "sub_major" in types:
            if sub_major:
                blocks += _leaves(
                    aos[sub_major], f"SubMajor:{aos[sub_major].title}/", "sub_major"
                )
        else:
            wrapper = course.model_copy(update={"blocks": [container]})
            blocks += _leaves(wrapper, "", _compute_core_or_project)
            for level in [container, *container.children]:
                for item in level.items:
                    if item.item_type == "stream" and item.code in aos:
                        blocks += _leaves(
                            aos[item.code], f"Elective:{item.code}/", "elective"
                        )
    return blocks
