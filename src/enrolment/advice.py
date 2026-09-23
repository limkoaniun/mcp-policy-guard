"""Advice tasks: one checkable answer each, truth from the exact evaluator.

The RAG arm cannot act, so on action tasks its numbers are decided before
any run. It is measured here instead, on questions where talking is enough,
against the guarded agent on the same questions. Two kinds:

- eligibility: the compliance sample, verbatim, asked as a question;
- session: "what could I take in <session>?", truth from `session_options`.

Scoring is string parsing and nothing else. A reply that breaks the format
is `answered: false` -- its own column, never folded into wrong -- so the
report can say how often a model ignored the instruction rather than how
often it was wrong.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .agent.sampling import SampledScenario
from .models import Course, Student, Subject
from .progression import session_options

SESSIONS = ("Autumn", "Spring")
TASKS = "advice_tasks.jsonl"

ELIGIBILITY_QUESTION = (
    "Am I eligible to enrol in {subject} next session? Begin your reply with "
    "exactly one of the words ELIGIBLE or NOT ELIGIBLE, then explain."
)
SESSION_QUESTION = (
    "What subjects could I take in {session}? List every subject code you "
    "would recommend, one per line, before any explanation."
)

CODE = re.compile(r"\b\d{5}\b")
FIRST_WORDS = re.compile(r"[^A-Za-z]*([A-Za-z]+)(?:[^A-Za-z]+([A-Za-z]+))?")


class AdviceTask(BaseModel):
    task_id: str
    kind: Literal["eligibility", "session"]
    student_id: str
    question: str
    expected_eligible: bool | None = None
    expected_codes: list[str] | None = None
    subject_code: str | None = None
    session: str | None = None
    rule_depth: int | None = None
    stratum: str | None = None


class AdviceVerdict(BaseModel):
    task_id: str
    kind: str
    answered: bool
    correct: bool | None = None
    jaccard: float | None = None
    extracted: str | list[str] = ""


def eligibility_tasks(sampled: list[SampledScenario], names: dict[str, str]) -> list[AdviceTask]:
    tasks = []
    for s in sampled:
        subject = f"{s.subject_code} {names.get(s.subject_code, '')}".strip()
        tasks.append(AdviceTask(
            task_id=f"E-{s.scenario_id}", kind="eligibility", student_id=s.student_id,
            question=ELIGIBILITY_QUESTION.format(subject=subject),
            expected_eligible=s.expected_satisfied, subject_code=s.subject_code,
            rule_depth=s.rule_depth, stratum=s.stratum,
        ))
    return tasks


def session_tasks(
    students: dict[str, Student], course: Course, aos: dict[str, Course],
    catalogue: dict[str, Subject], sessions: tuple[str, ...] = SESSIONS,
) -> list[AdviceTask]:
    tasks = []
    for student_id in sorted(students):
        for session in sessions:
            options = session_options(students[student_id], course, aos, catalogue, session)
            expected = sorted({code for codes in options.values() for code in codes})
            tasks.append(AdviceTask(
                task_id=f"S-{student_id}-{session.lower()}", kind="session",
                student_id=student_id, question=SESSION_QUESTION.format(session=session),
                expected_codes=expected, session=session,
            ))
    return tasks


def score(task: AdviceTask, text: str) -> AdviceVerdict:
    text = text or ""
    if task.kind == "eligibility":
        m = FIRST_WORDS.match(text)
        first = (m.group(1) or "").upper() if m else ""
        second = (m.group(2) or "").upper() if m else ""
        if first == "ELIGIBLE":
            said = True
        elif first == "NOT" and second == "ELIGIBLE":
            said = False
        else:
            return AdviceVerdict(task_id=task.task_id, kind=task.kind, answered=False, extracted=first)
        return AdviceVerdict(
            task_id=task.task_id, kind=task.kind, answered=True,
            correct=(said is task.expected_eligible), extracted="ELIGIBLE" if said else "NOT ELIGIBLE",
        )
    codes = list(dict.fromkeys(CODE.findall(text)))
    expected = set(task.expected_codes or [])
    # `not codes and expected` is "no answer": a reply with no codes when some
    # were due. When `expected` is itself empty, no codes is the *correct*
    # empty answer, not a missing one -- it falls through to the comparison
    # below, where `got == expected` (both empty sets) scores it correct and
    # rewards it. No current task has an empty expectation, but the scorer
    # must still get this right rather than by accident.
    if not codes and expected:
        return AdviceVerdict(task_id=task.task_id, kind=task.kind, answered=False, extracted=[])
    got = set(codes)
    union = got | expected
    jaccard = 1.0 if not union else len(got & expected) / len(union)
    return AdviceVerdict(
        task_id=task.task_id, kind=task.kind, answered=True,
        correct=(got == expected), jaccard=jaccard, extracted=codes,
    )


def write_tasks(campaign_dir, tasks: list[AdviceTask]) -> Path:
    path = Path(campaign_dir) / TASKS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(t.model_dump_json() + "\n" for t in tasks), encoding="utf-8")
    return path


def read_tasks(campaign_dir) -> list[AdviceTask]:
    path = Path(campaign_dir) / TASKS
    return [AdviceTask.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
