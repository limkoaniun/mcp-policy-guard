"""Conversation routing for the demo; batch advice prompts stay unchanged."""

import re

SEASONS = {"autumn": "Autumn", "spring": "Spring", "summer": "Summer",
           "秋季": "Autumn", "春季": "Spring", "夏季": "Summer"}


def resolve_question(question, *, history, catalogue, previous_session, pending):
    """Return retrieval query, session, and an optional clarification.

    Resolve references only from user messages, never from a model's invented
    course code. Relative academic sessions need an explicit choice; the
    machine's calendar is not an academic calendar.
    """
    from ..rag import find_subjects

    named = {value for key, value in SEASONS.items()
             if re.search(r"\b" + key + r"\b", question, re.IGNORECASE) or key in question and not key.isascii()}
    session = next(iter(named)) if len(named) == 1 else previous_session
    query = f"{pending}\n{question}" if pending and len(named) == 1 else question
    relative = bool(re.search(r"\bnext\s+(?:session|semester|term)\b|下学期", question, re.IGNORECASE))
    planning = bool(re.search(r"\b(session|semester|term|autumn|spring|summer)\b|学期|春季|秋季|夏季", query, re.IGNORECASE))
    if len(named) > 1 or (planning and not session) or (relative and not named):
        return query, None, "Which academic session do you mean: Autumn, Spring, or Summer?"
    referring = re.search(r"\b(it|its|that|this|those|these|same)\b|这门|那门|它", question, re.IGNORECASE)
    if not find_subjects(question, catalogue) and referring:
        for message in reversed(history):
            if message.get("role") == "user":
                codes = find_subjects(message["content"], catalogue)
                if codes:
                    query += "\nReferenced subjects: " + ", ".join(codes)
                    break
    return query, session if planning or referring else None, None
