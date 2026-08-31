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

import collections
from collections.abc import Callable, Sequence
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

MAX_PRIORITY = max(PRIORITY.values())


class Placement(BaseModel):
    """One subject's credit points, counted against one block."""

    code: str
    block: str
    credit_points: int


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


def assign(
    completed: dict[str, int], blocks: Sequence[Block]
) -> tuple[int, list[Placement]]:
    """Assign completed subjects to blocks, maximising counted credit.

    Each subject is counted at most once and no block exceeds its quota.
    Solved as min-cost max-flow so the answer is optimal: a greedy pass gets
    this wrong in over a quarter of realistic cases (spec §2), and a wrong
    answer here is a student told they wasted a subject they did not waste.

    Where several assignments tie on total counted credit and block priority,
    which specific block a subject lands in is unspecified by the cost function.
    Downstream code must not depend on it.

    Returns the total credit counted and where each subject landed.
    """
    codes = sorted(completed)
    n, m = len(codes), len(blocks)
    if not n or not m:
        return 0, []

    source, sink = 0, n + m + 1
    size = sink + 1
    capacity = [[0] * size for _ in range(size)]
    cost = [[0] * size for _ in range(size)]
    neighbours: dict[int, set[int]] = collections.defaultdict(set)

    def edge(u: int, v: int, cap: int, price: int) -> None:
        capacity[u][v] += cap
        cost[u][v] = price
        cost[v][u] = -price
        neighbours[u].add(v)
        neighbours[v].add(u)

    for i, code in enumerate(codes):
        edge(source, 1 + i, completed[code], 0)
    for j, block in enumerate(blocks):
        edge(n + 1 + j, sink, block.quota, 0)
        for i, code in enumerate(codes):
            if code in block.eligible:
                edge(1 + i, n + 1 + j, completed[code], MAX_PRIORITY - PRIORITY[block.kind])

    counted = 0
    while True:
        distance = {source: 0}
        parent: dict[int, int | None] = {source: None}
        queue = collections.deque([source])
        queued = {source}
        while queue:
            u = queue.popleft()
            queued.discard(u)
            for v in neighbours[u]:
                if capacity[u][v] > 0 and distance[u] + cost[u][v] < distance.get(v, 1 << 30):
                    distance[v] = distance[u] + cost[u][v]
                    parent[v] = u
                    if v not in queued:
                        queue.append(v)
                        queued.add(v)
        if sink not in parent:
            break
        path, node = [], sink
        while parent[node] is not None:
            path.append((parent[node], node))
            node = parent[node]
        pushed = min(capacity[u][v] for u, v in path)
        for u, v in path:
            capacity[u][v] -= pushed
            capacity[v][u] += pushed
        counted += pushed

    placements = [
        Placement(code=code, block=block.path, credit_points=capacity[n + 1 + j][1 + i])
        for i, code in enumerate(codes)
        for j, block in enumerate(blocks)
        if code in block.eligible and capacity[n + 1 + j][1 + i] > 0
    ]
    return counted, placements
