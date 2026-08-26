"""Synthetic student records.

Every student here is invented. Only the catalogue and requisite data is
real, which is why this case study needs no ethics approval.

Generation is seeded so a cohort is reproducible: the same seed must always
produce the same students, or experiment runs are not comparable.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path

from .models import CompletedSubject, Student, Subject

FEIT_DESCRIPTOR = "Master's Coursework courses owned by FEIT"
SESSIONS = ["2024-AUT", "2024-SPR", "2025-AUT", "2025-SPR"]


def _pick(rng: random.Random, value: str | Sequence[str] | None, field: str) -> str | None:
    """Resolve a single value or a pool of values to one value for one student.

    A single string is used as-is and draws nothing from `rng`, so the existing
    call signature produces exactly the cohort it always did. A sequence is
    sampled per student, which is what lets a cohort vary in the fields that
    admission- and aos-gated requisites turn on -- otherwise every student
    carries the same answer and those requisites have a constant expected
    result across the whole suite.
    """
    if value is None or isinstance(value, str):
        return value
    pool = list(value)
    if not pool:
        # An empty pool would either crash inside rng.choice or, if defaulted
        # to None, quietly produce the constant cohort the caller asked to
        # avoid. Say so instead.
        raise ValueError(f"{field} was given an empty sequence; pass None or at least one value")
    return rng.choice(pool)


def generate_students(
    catalogue: dict[str, Subject],
    count: int = 20,
    seed: int = 0,
    course: str = "C04296",
    major: str | Sequence[str] | None = None,
    sub_major: str | Sequence[str] | None = None,
) -> list[Student]:
    """Generate a reproducible synthetic cohort.

    `major` and `sub_major` each accept a single value shared by every student,
    or a sequence sampled from per student. Sampling uses the same seeded
    generator as the rest of the cohort, so a seed still reproduces a cohort
    exactly.
    """
    rng = random.Random(seed)
    codes = sorted(catalogue)
    students: list[Student] = []

    for index in range(count):
        history_size = rng.randint(0, min(8, len(codes)))
        chosen = rng.sample(codes, history_size)
        completed = [
            CompletedSubject(
                code=code,
                session=SESSIONS[position % len(SESSIONS)],
                credit_points=catalogue[code].credit_points,
            )
            for position, code in enumerate(sorted(chosen))
        ]
        remaining = [code for code in codes if code not in chosen]
        enrolled = rng.sample(remaining, min(1, len(remaining)))
        students.append(
            Student(
                student_id=f"S{1000001 + index}",
                course=course,
                admitted_to=[course, FEIT_DESCRIPTOR],
                major=_pick(rng, major, "major"),
                sub_major=_pick(rng, sub_major, "sub_major"),
                completed=completed,
                enrolled=enrolled,
            )
        )
    return students


def write_students(path, students: list[Student]) -> None:
    payload = {student.student_id: student.model_dump() for student in students}
    Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
