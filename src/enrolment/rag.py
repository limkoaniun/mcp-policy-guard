"""The RAG chatbot: deterministic retrieval, one completion, no tools.

The comparison arm the research question names. It sees the raw material a
student would -- the handbook entry, the published requisite rule and item
texts, the student's own record, the published course structure for a
session question, and any uploaded document -- and must reason to an answer.
It never sees the exact evaluator's verdict or its list of options: that
would make it an agent with one tool and a different name. The course
structure in particular is rendered unfiltered -- every block, every subject
it accepts, whether or not the student has completed it, whether or not the
block is already full, whether or not the subject runs this session. The
model must apply those three conditions itself; none is pre-applied.

Retrieval is keyword and code matching, not embeddings. The report describes
it as "retrieval-augmented generation with a deterministic keyword retriever"
and does not overclaim.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import BaseModel, Field

from .agent.prompts import SYSTEM_PROMPT
from .agent.transcript import ChatReply
from .models import Course, Student, Subject, SubjectRequisites
from .progression import expand_structure

Chat = Callable[[list[dict], list[dict]], ChatReply]

CODE = re.compile(r"\b\d{5}\b")
WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
#: Words that appear in most subject names or descriptions and would retrieve
#: half the catalogue. Kept short and stated; a keyword retriever is honest
#: about being one.
STOPWORDS = {
    "what", "which", "could", "would", "should", "take", "next", "session",
    "subject", "subjects", "about", "eligible", "enrol", "enrolment", "with",
    "that", "this", "have", "from", "into", "list", "every", "reply", "your",
    "explain", "begin", "then", "before", "after", "recommend", "code", "codes",
    "line", "words", "exactly", "autumn", "spring", "summer", "student",
    "please", "help", "attached", "file", "form", "request", "want", "like",
}


def find_subjects(question: str, catalogue: dict[str, Subject]) -> list[str]:
    """Codes mentioned in the question, plus subjects whose name shares a keyword.

    A code the question names is a precise retrieval; a keyword is a guess.
    So when the question names at least one catalogue code, that is the
    whole answer -- a question about 32513 that happens to quote its long
    name must not also retrieve every subject whose name shares a word with
    it. Keyword expansion only runs when no code was mentioned.
    """
    named = {code for code in CODE.findall(question) if code in catalogue}
    if named:
        return sorted(named)
    words = {w.lower() for w in WORD.findall(question)} - STOPWORDS
    found = set()
    for code, subject in catalogue.items():
        name = subject.name.lower()
        if any(re.search(r"\b" + re.escape(word), name) for word in words):
            found.add(code)
    return sorted(found)


def _items(label: str, rule: str | None, items: dict) -> str:
    if not rule:
        return ""
    lines = [f"{label} rule: {rule}"]
    for key in sorted(items, key=lambda k: (len(k), k)):
        lines.append(f"  {key}: {items[key].text}")
    return "\n".join(lines)


def render_subject(subject: Subject, requisites: SubjectRequisites | None) -> str:
    """One handbook entry: the subject, then its published rules verbatim."""
    head = (
        f"Subject {subject.code} {subject.name} ({subject.credit_points} credit points; "
        f"offered: {', '.join(subject.sessions) or 'not published'})\n"
        f"{subject.description.strip()}"
    )
    if requisites is None or not (requisites.requisite_rule or requisites.antirequisite_rule):
        return head + "\nNo requisites published for this subject."
    parts = [head]
    req = _items("Requisite", requisites.requisite_rule, requisites.requisite_items)
    anti = _items("Anti-requisite", requisites.antirequisite_rule, requisites.antirequisite_items)
    if req:
        parts.append(req)
    if anti:
        parts.append(anti)
    return "\n".join(parts)


def render_record(student: Student) -> str:
    completed = ", ".join(
        f"{c.code} ({c.session}, {c.credit_points}cp)" for c in student.completed
    ) or "none"
    return (
        f"Student record for {student.student_id}\n"
        f"Course: {student.course}\n"
        f"Admitted to: {'; '.join(student.admitted_to) or 'not recorded'}\n"
        f"Major: {student.major or 'none'}; sub-major: {student.sub_major or 'none'}\n"
        f"Completed subjects: {completed}\n"
        f"Currently enrolled: {', '.join(student.enrolled) or 'none'}\n"
        f"Credit-point cap per session: {student.credit_point_cap_per_session}"
    )


def render_structure(
    student: Student,
    course: Course,
    aos: dict[str, Course],
    catalogue: dict[str, Subject],
    requisites: dict[str, SubjectRequisites],
    session: str,
) -> str:
    """The published course structure, unfiltered.

    Every block `expand_structure` returns, and every code it accepts that is
    in the catalogue -- whether or not the student has completed it, whether
    or not the block is already full, whether or not the subject is offered
    in `session`. This is the handbook itself, not the evaluator's answer:
    the three conditions `session_options` applies (unfilled block, not
    completed, offered in the session) are what the model must apply to this
    text itself; none of them is pre-applied here.

    `requisites` and `session` are accepted for signature stability with the
    caller and are not otherwise used here: requisites are already rendered
    per subject by `render_subject`, and `session` is not used to filter --
    it names the session so the model knows which "offered" column to read.
    """
    blocks = expand_structure(course, aos, student.major, student.sub_major)
    lines = [f"Course structure for {student.course}:"]
    for block in blocks:
        lines.append(f"Block {block.path} (quota {block.quota} credit points)")
        for code in sorted(block.eligible):
            if code not in catalogue:
                continue
            subject = catalogue[code]
            lines.append(
                f"  {code} {subject.name} ({subject.credit_points}cp; "
                f"offered: {', '.join(subject.sessions) or 'not published'})"
            )
    return "\n".join(lines)


def render_attachments(attachments: dict[str, dict] | None) -> str:
    if not attachments:
        return ""
    return "\n\n".join(
        f"Uploaded document {entry.get('name', key)}:\n{entry.get('text', '')}"
        for key, entry in attachments.items()
    )


class RagReply(BaseModel):
    """What the chatbot said, and exactly what it was shown."""

    text: str
    context: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0


def answer(
    question: str,
    *,
    student: Student,
    catalogue: dict[str, Subject],
    requisites: dict[str, SubjectRequisites],
    course: Course,
    aos: dict[str, Course],
    chat: Chat,
    model: str,
    system_prompt: str = SYSTEM_PROMPT,
    attachments: dict[str, dict] | None = None,
    session: str | None = None,
) -> RagReply:
    """Retrieve, stuff, answer. One completion; no tools are offered.

    The system prompt is the agent's own with the principal filled, so the
    two arms differ in what they can do, not in how they are addressed.
    `model` is recorded by the caller; it is accepted here so the signature
    matches the agent arm's and a runner can treat the two alike.
    """
    blocks: list[str] = []
    for code in find_subjects(question, catalogue):
        blocks.append(render_subject(catalogue[code], requisites.get(code)))
    blocks.append(render_record(student))
    if session:
        blocks.append(render_structure(student, course, aos, catalogue, requisites, session))
    docs = render_attachments(attachments)
    if docs:
        blocks.append(docs)
    user = "Documents:\n\n" + "\n\n".join(blocks) + "\n\n" + question
    messages = [
        {"role": "system", "content": system_prompt.replace("{principal}", student.student_id)},
        {"role": "user", "content": user},
    ]
    reply = chat(messages, [])
    return RagReply(text=reply.content or "", context=blocks, latency_ms=reply.latency_ms)
