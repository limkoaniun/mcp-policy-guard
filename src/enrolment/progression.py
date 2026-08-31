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

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

from .models import Course, CourseBlock, Student

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

# Score scale for lexicographic ordering: maximise counted credit first, then priority-weighted
# placement. We encode as one integer: score = cp * SCALE + cp * PRIORITY[block.kind].
# SCALE must exceed the maximum achievable priority sum. With 96 cp maximum and PRIORITY[core]=40,
# the ceiling is 96 * 40 = 3840. SCALE = 100_000 comfortably exceeds this.
SCALE = 100_000


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

    Each subject is an indivisible whole: it is counted entirely toward one block,
    or not at all. No subject is split across blocks. Solved by exact whole-subject
    search, which is proven optimal: it maximises total counted credit first, then
    applies the priority-based tie-break (spec §7) among all equally-maximum solutions.
    A greedy pass gets this wrong in over a quarter of realistic cases (spec §2).

    Where several assignments tie on total counted credit and block priority,
    which specific block a subject lands in is unspecified by the algorithm.
    Downstream code must not depend on it.

    Returns the total credit counted and where each subject landed.
    """
    if not completed or not blocks:
        return 0, []

    # Sort subjects by descending credit points, then by code, for stable order.
    subjects = sorted(completed.items(), key=lambda x: (-x[1], x[0]))
    codes = [code for code, _ in subjects]
    cps = [cp for _, cp in subjects]
    n = len(subjects)

    # Precompute per-subject eligible block indices.
    eligible: list[list[int]] = []
    for code in codes:
        indices = [j for j, block in enumerate(blocks) if code in block.eligible]
        eligible.append(indices)

    # Convert blocks sequence to list for indexing.
    blocks_list = list(blocks)

    memo: dict[tuple[int, tuple[int, ...]], tuple[int, dict[int, int]]] = {}

    def recurse(
        subject_idx: int,
        remaining_quotas: tuple[int, ...]
    ) -> tuple[int, dict[int, int]]:
        """Recurse over subjects. Return (score, assignments) where assignments maps
        subject index to block index (or is absent if not assigned)."""

        # Base case: all subjects processed.
        if subject_idx >= n:
            return 0, {}

        # Memoization: check if we've seen this state before.
        key = (subject_idx, remaining_quotas)
        if key in memo:
            return memo[key]

        cp = cps[subject_idx]

        # Baseline: do not assign this subject.
        best_local_score, best_local_assignments = recurse(subject_idx + 1, remaining_quotas)

        # Try assigning this subject whole to each eligible block with room.
        for block_idx in eligible[subject_idx]:
            if remaining_quotas[block_idx] >= cp:
                # Assign subject to this block.
                new_quotas = list(remaining_quotas)
                new_quotas[block_idx] -= cp
                score, future_assignments = recurse(subject_idx + 1, tuple(new_quotas))
                # Score for placing this subject: cp * SCALE + cp * priority.
                block_priority = PRIORITY[blocks_list[block_idx].kind]
                local_score = score + cp * SCALE + cp * block_priority
                if local_score > best_local_score:
                    best_local_score = local_score
                    best_local_assignments = {subject_idx: block_idx, **future_assignments}

        # Memoize.
        memo[key] = (best_local_score, best_local_assignments)
        return best_local_score, best_local_assignments

    initial_quotas = tuple(block.quota for block in blocks_list)
    _, assignments = recurse(0, initial_quotas)

    # Convert assignments to Placement objects.
    placements = []
    for subj_idx, block_idx in assignments.items():
        placements.append(
            Placement(
                code=codes[subj_idx],
                block=blocks_list[block_idx].path,
                credit_points=cps[subj_idx]
            )
        )

    total_counted = sum(p.credit_points for p in placements)
    return total_counted, placements


class PlanSummary(BaseModel):
    """Where a student's completed subjects land, and what is still required."""

    major: str | None = None
    sub_major: str | None = None
    counted: int
    placements: list[Placement] = Field(default_factory=list)
    unplaced: list[str] = Field(default_factory=list)
    counted_in_major: int
    remaining_in_major: int
    remaining_by_block: dict[str, int] = Field(default_factory=dict)
    total_required: int
    remaining_total: int


def summarise_plan(student: Student, course: Course, aos: dict[str, Course]) -> PlanSummary:
    """The student's progress through their declared major and sub-major.

    Credit points come from the student's own record rather than the
    catalogue: a record may carry subjects from outside this course (prior
    study, a transfer), and a catalogue lookup would raise on them instead
    of reporting them as counting nowhere.
    """
    blocks = expand_structure(course, aos, student.major, student.sub_major)
    completed = {c.code: c.credit_points for c in student.completed}
    counted, placements = assign(completed, blocks)

    placed = {p.code for p in placements}
    used: dict[str, int] = {}
    for placement in placements:
        used[placement.block] = used.get(placement.block, 0) + placement.credit_points

    in_major = sum(p.credit_points for p in placements if p.block.startswith("Major:"))
    major_quota = sum(b.quota for b in blocks if b.kind == "major")
    total_required = course.credit_points or 0

    return PlanSummary(
        major=student.major,
        sub_major=student.sub_major,
        counted=counted,
        placements=placements,
        unplaced=sorted(set(completed) - placed),
        counted_in_major=in_major,
        remaining_in_major=major_quota - in_major,
        remaining_by_block={b.path: b.quota - used.get(b.path, 0) for b in blocks},
        total_required=total_required,
        remaining_total=max(0, total_required - counted),
    )


class MajorComparison(BaseModel):
    """What changing major would cost this student."""

    from_major: str | None = None
    to_major: str
    counted_in_current_major: int
    counted_in_target_major: int
    remaining_in_target_major: int
    displaced: list[str] = Field(default_factory=list)
    unplaced: list[str] = Field(default_factory=list)
    current: PlanSummary
    target: PlanSummary


def compare_major(
    student: Student, course: Course, aos: dict[str, Course], target_major: str
) -> MajorComparison:
    """Re-run the plan against a different major and report the difference.

    `displaced` is the subjects that count inside the current major but not
    inside the target -- the concrete cost of the change. They may still count
    elsewhere (electives, a sub-major), which is why it is reported separately
    from `unplaced`, the subjects that count nowhere at all.
    """
    if target_major not in aos:
        raise KeyError(
            f"{target_major} is not a known area of study; "
            f"cannot compare a major that does not exist"
        )

    current = summarise_plan(student, course, aos)
    switched = student.model_copy(update={"major": target_major})
    target = summarise_plan(switched, course, aos)

    in_current = {p.code for p in current.placements if p.block.startswith("Major:")}
    in_target = {p.code for p in target.placements if p.block.startswith("Major:")}

    return MajorComparison(
        from_major=student.major,
        to_major=target_major,
        counted_in_current_major=current.counted_in_major,
        counted_in_target_major=target.counted_in_major,
        remaining_in_target_major=target.remaining_in_major,
        displaced=sorted(in_current - in_target),
        unplaced=target.unplaced,
        current=current,
        target=target,
    )
