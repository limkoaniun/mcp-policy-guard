"""Data models for the enrolment case study.

Only catalogue and requisite data is real; every student record is synthetic.
Records that come from a UTS page carry `source_url` and `fetched_at` so the
dataset's provenance is auditable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal["subject", "credit_points", "aos", "admission", "unparsed"]
ItemType = Literal["academic", "admission", "other", "unknown"]


class RequisiteItem(BaseModel):
    """One numbered row of a requisite or anti-requisite table.

    `text` always holds the raw published wording, including when parsing
    failed, so no requisite is ever silently lost.
    """

    label: str
    kind: ItemKind
    type: ItemType = "unknown"
    text: str
    subject: str | None = None
    aos_code: str | None = None
    course: str | None = None
    credit_points: int | None = None
    admission_descriptor: str | None = None
    corequisite: bool = False


class SubjectRequisites(BaseModel):
    """Both rules published for one subject. Absent rules mean no requisites."""

    code: str
    requisite_rule: str | None = None
    requisite_items: dict[str, RequisiteItem] = Field(default_factory=dict)
    antirequisite_rule: str | None = None
    antirequisite_items: dict[str, RequisiteItem] = Field(default_factory=dict)
    source_url: str
    fetched_at: str


class Subject(BaseModel):
    code: str
    name: str
    credit_points: int
    description: str = ""
    sessions: list[str] = Field(default_factory=list)
    source_url: str
    fetched_at: str


class BlockItem(BaseModel):
    """A subject, major, sub-major or stream referenced by a course block.

    `connector` is the AND/OR joining this item to its siblings, as published
    in the handbook's `parent_connector` field.
    """

    code: str
    name: str
    credit_points: int | None = None
    item_type: str
    url: str | None = None
    connector: str | None = None


class CourseBlock(BaseModel):
    title: str
    description: str = ""
    credit_points: int | None = None
    items: list[BlockItem] = Field(default_factory=list)
    children: list[CourseBlock] = Field(default_factory=list)


class Course(BaseModel):
    code: str
    title: str
    credit_points: int | None = None
    blocks: list[CourseBlock] = Field(default_factory=list)
    source_url: str
    fetched_at: str


class CompletedSubject(BaseModel):
    code: str
    session: str
    credit_points: int


class Student(BaseModel):
    """Synthetic student record.

    `admitted_to` holds both course codes (e.g. "C04296") and the free-text
    admission descriptors UTS uses (e.g. "Master's Coursework courses owned
    by FEIT"), because admission requisites are published in both forms.
    """

    student_id: str
    course: str
    admitted_to: list[str] = Field(default_factory=list)
    major: str | None = None
    sub_major: str | None = None
    completed: list[CompletedSubject] = Field(default_factory=list)
    enrolled: list[str] = Field(default_factory=list)
    credit_point_cap_per_session: int = 24

    def completed_codes(self) -> set[str]:
        return {entry.code for entry in self.completed}

    def completed_credit_points(self) -> int:
        return sum(entry.credit_points for entry in self.completed)


class ItemOutcome(BaseModel):
    label: str
    satisfied: bool
    reason: str


class RequisiteResult(BaseModel):
    """The engine's exact ruling on whether a student may enrol in a subject.

    `explanation` is written to be read by the agent: it becomes the body of
    the proxy's deny message, which is how a policy block turns into a
    helpful sentence rather than an opaque error.
    """

    subject_code: str
    satisfied: bool
    rule: str | None = None
    items: dict[str, ItemOutcome] = Field(default_factory=dict)
    unmet: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    blocked_by_antirequisite: bool = False
    explanation: str = ""
