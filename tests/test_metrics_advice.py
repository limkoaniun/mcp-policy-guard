"""Scoring advice runs: no answer is a column, exclusions are few."""

from enrolment.advice import AdviceTask, write_tasks
from enrolment.agent.campaign import IndexRecord, write_index
from enrolment.agent.transcript import ResponseOutcome, Transcript, TurnRecord
from enrolment.metrics import advice as scorer


def task(kind="eligibility", **over):
    base = {"task_id": "E-S1-32933", "kind": "eligibility", "student_id": "S1", "question": "q",
             "expected_eligible": False, "subject_code": "32933", "rule_depth": 3, "stratum": "core"}
    if kind == "session":
        base = {"task_id": "S-S1-autumn", "kind": "session", "student_id": "S1", "question": "q",
                 "expected_codes": ["32009"], "session": "Autumn"}
    base.update(over)
    return AdviceTask(**base)


def write_run(campaign_dir, run_id, text, *, truncated=False, last_is_call=False):
    run_dir = campaign_dir / run_id
    run_dir.mkdir(parents=True)
    turns = [TurnRecord(user_turn=0, model_turn=0, outcome=ResponseOutcome.TEXT, text=text)]
    if last_is_call:
        turns.append(TurnRecord(user_turn=0, model_turn=1, outcome=ResponseOutcome.TOOL_CALLS, text=""))
    Transcript(task_id=run_id.split("-fake")[0], model="fake", turns=turns,
               truncated=truncated, truncation_reason="bound" if truncated else None
               ).write_jsonl(run_dir / "transcript.jsonl")
    return run_dir


def record(run_id, arm, status="ok"):
    return IndexRecord(run_id=run_id, scenario_id=run_id.split("-fake")[0], attack_class=None,
                       model="fake", arm=arm, repeat=1, attempt=0, status=status, duration_s=1.0)


def test_final_text_is_the_last_text_turn_or_empty():
    t = Transcript(task_id="x", model="m", turns=[
        TurnRecord(user_turn=0, model_turn=0, outcome=ResponseOutcome.TOOL_CALLS, text=""),
        TurnRecord(user_turn=0, model_turn=1, outcome=ResponseOutcome.TEXT, text="NOT ELIGIBLE."),
    ])
    assert scorer.final_text(t) == "NOT ELIGIBLE."
    assert scorer.final_text(Transcript(task_id="x", model="m")) == ""


def test_a_correct_run_is_judged_correct(tmp_path):
    run_dir = write_run(tmp_path, "E-S1-32933-fake-rag-r1", "NOT ELIGIBLE, you lack 32144.")
    j = scorer.judge_run(record("E-S1-32933-fake-rag-r1", "rag"), run_dir, task())
    assert j["scoreable"] and j["answered"] and j["correct"] is True


def test_judge_run_includes_the_wall_clock_duration(tmp_path):
    run_dir = write_run(tmp_path, "E-S1-32933-fake-rag-r1", "NOT ELIGIBLE, you lack 32144.")
    j = scorer.judge_run(record("E-S1-32933-fake-rag-r1", "rag"), run_dir, task())
    assert j["duration_s"] == 1.0


def test_judge_run_includes_the_expected_eligibility_for_eligibility_tasks(tmp_path):
    run_dir = write_run(tmp_path, "E-S1-32933-fake-rag-r1", "NOT ELIGIBLE, you lack 32144.")
    j = scorer.judge_run(record("E-S1-32933-fake-rag-r1", "rag"), run_dir, task())
    assert j["expected"] is False


def test_judge_run_expected_is_none_for_session_tasks(tmp_path):
    run_dir = write_run(tmp_path, "S-S1-autumn-fake-rag-r1", "32009")
    j = scorer.judge_run(record("S-S1-autumn-fake-rag-r1", "rag"), run_dir, task("session"))
    assert j["expected"] is None


def test_a_run_whose_last_turn_is_a_call_has_no_answer(tmp_path):
    run_dir = write_run(tmp_path, "E-S1-32933-fake-agent-r1", "", last_is_call=True)
    j = scorer.judge_run(record("E-S1-32933-fake-agent-r1", "agent"), run_dir, task())
    assert j["scoreable"] and j["answered"] is False and j["correct"] is None


def test_a_truncated_run_is_excluded_not_scored(tmp_path):
    run_dir = write_run(tmp_path, "E-S1-32933-fake-agent-r1", "NOT ELIGIBLE", truncated=True)
    j = scorer.judge_run(record("E-S1-32933-fake-agent-r1", "agent", status="truncated"), run_dir, task())
    assert j["scoreable"] is False and j["excluded_because"] == "truncated"


def test_aggregate_reports_answered_and_both_accuracies_per_kind_arm_and_model():
    judgements = [
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "core"},
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": False, "jaccard": None, "rule_depth": 3, "stratum": "core"},
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": False, "correct": None, "jaccard": None, "rule_depth": 1, "stratum": "core"},
        {"scoreable": True, "kind": "eligibility", "arm": "agent", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "top_up"},
        {"scoreable": False, "kind": "eligibility", "arm": "agent", "model": "fake", "excluded_because": "truncated"},
        {"scoreable": True, "kind": "session", "arm": "rag", "model": "fake", "answered": True, "correct": False, "jaccard": 0.5},
    ]
    m = scorer.aggregate(judgements)
    e = m["eligibility"]["rag"]["fake"]
    assert e["answered"] == {"numerator": 2, "denominator": 3}
    assert e["accuracy_over_answered"] == {"numerator": 1, "denominator": 2}
    assert e["accuracy_over_all"] == {"numerator": 1, "denominator": 3}
    assert m["eligibility"]["rag"]["fake"]["by_depth"]["3"] == {"numerator": 1, "denominator": 2}
    assert m["eligibility"]["agent"]["fake"]["by_depth"] == {}, "top_up never enters the depth table"
    assert m["session"]["rag"]["fake"]["mean_jaccard"] == 0.5
    assert m["exclusions"] == {"truncated": 1}
    assert m["scored"] == 5 and m["total"] == 6


def test_aggregate_reports_mean_duration_s_per_cell():
    judgements = [
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "core", "duration_s": 2.0},
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "core", "duration_s": 4.0},
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "core", "duration_s": None},
    ]
    m = scorer.aggregate(judgements)
    assert m["eligibility"]["rag"]["fake"]["mean_duration_s"] == 3.0


def test_aggregate_reports_the_majority_class_baseline_for_eligibility():
    judgements = [
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "core", "expected": False},
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": False, "correct": None, "jaccard": None, "rule_depth": 1, "stratum": "core", "expected": False},
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": False, "jaccard": None, "rule_depth": 3, "stratum": "core", "expected": True},
    ]
    m = scorer.aggregate(judgements)
    assert m["eligibility"]["rag"]["fake"]["majority_baseline"] == {"numerator": 2, "denominator": 3}


def test_aggregate_mean_duration_s_is_none_when_nothing_was_timed():
    judgements = [
        {"scoreable": True, "kind": "eligibility", "arm": "rag", "model": "fake", "answered": True, "correct": True, "jaccard": None, "rule_depth": 3, "stratum": "core", "duration_s": None},
    ]
    m = scorer.aggregate(judgements)
    assert m["eligibility"]["rag"]["fake"]["mean_duration_s"] is None


def test_main_writes_metrics_and_judgements(tmp_path, capsys):
    campaign = tmp_path / "c"
    campaign.mkdir()
    write_tasks(campaign, [task()])
    write_run(campaign, "E-S1-32933-fake-rag-r1", "NOT ELIGIBLE.")
    write_index(campaign, [record("E-S1-32933-fake-rag-r1", "rag")])
    out = tmp_path / "scored"
    assert scorer.main(["--campaign", str(campaign), "--out", str(out)]) == 0
    assert (out / "metrics.json").exists() and (out / "judgements.jsonl").exists()
    assert "eligibility" in capsys.readouterr().out
