"""Advice tasks: truth from the exact evaluator, scoring by string parsing."""

import json
import pathlib

from enrolment.advice import (
    SESSIONS,
    AdviceTask,
    eligibility_tasks,
    read_tasks,
    score,
    session_tasks,
    write_tasks,
)
from enrolment.agent.sampling import stratified_sample
from enrolment.models import Course, Student, Subject
from enrolment.progression import session_options
from enrolment.scenarios import read_jsonl

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA = REPO / "data"


def load(path, model):
    return {k: model.model_validate(v) for k, v in json.loads(path.read_text()).items()}


CATALOGUE = load(DATA / "catalogue.json", Subject)
STUDENTS = load(DATA / "students.json", Student)
COURSE = Course.model_validate(json.loads((DATA / "course.json").read_text()))
AOS = load(DATA / "aos.json", Course)
NAMES = {code: s.name for code, s in CATALOGUE.items()}


def sample():
    return stratified_sample(
        read_jsonl(DATA / "compliance_scenarios.jsonl"),
        per_depth=40, mode_floor=25, seed=20260903,
    )


def test_eligibility_tasks_are_the_compliance_sample_with_its_truth():
    sampled = sample()
    tasks = eligibility_tasks(sampled, NAMES)
    assert len(tasks) == len(sampled) == 248
    by_id = {t.task_id: t for t in tasks}
    for s in sampled:
        t = by_id["E-" + s.scenario_id]
        assert t.kind == "eligibility"
        assert t.student_id == s.student_id
        assert t.subject_code == s.subject_code
        assert t.expected_eligible is s.expected_satisfied
        assert t.rule_depth == s.rule_depth and t.stratum == s.stratum
        assert s.subject_code in t.question and NAMES[s.subject_code] in t.question
        assert "ELIGIBLE or NOT ELIGIBLE" in t.question


def test_session_tasks_cover_every_student_and_session_with_the_evaluator_truth():
    tasks = session_tasks(STUDENTS, COURSE, AOS, CATALOGUE)
    assert len(tasks) == len(STUDENTS) * len(SESSIONS) == 100
    for t in tasks:
        assert t.kind == "session"
        expected = sorted({c for codes in session_options(
            STUDENTS[t.student_id], COURSE, AOS, CATALOGUE, t.session).values() for c in codes})
        assert t.expected_codes == expected
        assert t.session in t.question and "one per line" in t.question


def elig(expected=False):
    return AdviceTask(task_id="E-x", kind="eligibility", student_id="S1", question="q",
                      expected_eligible=expected, subject_code="32513")


def sess(expected):
    return AdviceTask(task_id="S-x", kind="session", student_id="S1", question="q",
                      expected_codes=expected, session="Autumn")


def test_eligibility_scoring_reads_the_first_word_case_and_markdown_insensitively():
    assert score(elig(False), "**NOT ELIGIBLE** — you lack 32130.").correct is True
    assert score(elig(False), "not eligible, sorry").correct is True
    assert score(elig(True), "Eligible! You meet every requisite.").correct is True
    v = score(elig(True), "NOT ELIGIBLE")
    assert v.answered is True and v.correct is False


def test_eligibility_scoring_reports_no_answer_for_a_reply_that_breaks_the_format():
    v = score(elig(False), "You are not able to enrol because 32130 is missing.")
    assert v.answered is False and v.correct is None
    assert score(elig(False), "").answered is False
    assert score(elig(False), "Not sure. NOT ELIGIBLE").answered is False, "must be the first word"


def test_session_scoring_compares_the_set_of_codes():
    v = score(sess(["32009", "32113"]), "32113\n32009\nThese suit you.")
    assert v.answered and v.correct is True and v.jaccard == 1.0
    assert v.extracted == ["32113", "32009"]
    v = score(sess(["32009", "32113"]), "32009 only.")
    assert v.correct is False and v.jaccard == 0.5


def test_session_scoring_no_codes():
    assert score(sess(["32009"]), "I cannot say.").answered is False
    empty = score(sess([]), "There is nothing you can take in Autumn.")
    assert empty.answered is True and empty.correct is True and empty.jaccard == 1.0


def test_tasks_round_trip_through_the_campaign_directory(tmp_path):
    tasks = [elig(False), sess(["32009"])]
    path = write_tasks(tmp_path, tasks)
    assert path.name == "advice_tasks.jsonl"
    assert read_tasks(tmp_path) == tasks
