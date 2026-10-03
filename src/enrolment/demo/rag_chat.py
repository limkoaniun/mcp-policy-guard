"""Conversation routing for the demo; batch advice prompts stay unchanged."""

import re

SEASONS = {"autumn": "Autumn", "spring": "Spring", "summer": "Summer",
           "秋季": "Autumn", "春季": "Spring", "夏季": "Summer"}

CLARIFICATION = "Which academic session do you mean: Autumn, Spring, or Summer?"


def resolve_question(question, *, history, catalogue, previous_session, pending):
    """Return retrieval query, session, and an optional clarification.

    Resolve references only from user messages, never from a model's invented
    course code. Relative academic sessions need an explicit choice; the
    machine's calendar is not an academic calendar.

    Only a *relative* session ("next semester", "this term") or an ambiguous
    one (two seasons named, and no subject whose own entry settles it) asks for
    a choice. Merely mentioning a session ("my credit cap per session") does
    not: that is a unit of measure, answerable from the record alone.

    While a clarification is pending, a short reply that still names no
    season ("the first one", "this semester") is asked again, and the pending
    question is kept; a reply that reads as a new question replaces it.
    """
    from ..rag import find_subjects

    named = {value for key, value in SEASONS.items()
             if re.search(r"\b" + key + r"\b", question, re.IGNORECASE) or key in question and not key.isascii()}
    subjects = find_subjects(question, catalogue)
    relative = bool(re.search(
        r"\b(?:next|this|coming|upcoming|following)\s+(?:session|semester|term)\b|下学期|这学期",
        question, re.IGNORECASE))
    if pending and len(named) != 1 and not subjects and len(question.split()) <= 5:
        return question, None, CLARIFICATION
    session = next(iter(named)) if len(named) == 1 else previous_session if not named else None
    query = f"{pending}\n{question}" if pending and len(named) == 1 else question
    planning = bool(re.search(r"\b(session|semester|term|autumn|spring|summer)\b|学期|春季|秋季|夏季", query, re.IGNORECASE))
    if (len(named) > 1 and not subjects) or (relative and len(named) != 1):
        return query, None, CLARIFICATION
    referring = re.search(r"\b(it|its|that|this|those|these|same)\b|这门|那门|它", question, re.IGNORECASE)
    if not subjects and referring:
        for message in reversed(history):
            if message.get("role") == "user":
                codes = find_subjects(message["content"], catalogue)
                if codes:
                    query += "\nReferenced subjects: " + ", ".join(codes)
                    break
    return query, session if planning or referring else None, None
