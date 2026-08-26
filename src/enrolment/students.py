"""Synthetic student records.

Every student here is invented. Only the catalogue and requisite data is
real, which is why this case study needs no ethics approval.

Generation is seeded so a cohort is reproducible: the same seed must always
produce the same students, or experiment runs are not comparable.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .models import CompletedSubject, Student, Subject

FEIT_DESCRIPTOR = "Master's Coursework courses owned by FEIT"
SESSIONS = ["2024-AUT", "2024-SPR", "2025-AUT", "2025-SPR"]


def generate_students(
    catalogue: dict[str, Subject],
    count: int = 20,
    seed: int = 0,
    course: str = "C04296",
    major: str | None = None,
) -> list[Student]:
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
        students.append(
            Student(
                student_id=f"S{1000001 + index}",
                course=course,
                admitted_to=[course, FEIT_DESCRIPTOR],
                major=major,
                completed=completed,
                enrolled=rng.sample(remaining, min(1, len(remaining))),
            )
        )
    return students


def write_students(path, students: list[Student]) -> None:
    payload = {student.student_id: student.model_dump() for student in students}
    Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
