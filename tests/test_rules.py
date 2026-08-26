import random

import pytest

from enrolment.rules import (
    And,
    Label,
    Or,
    RuleSyntaxError,
    depth,
    evaluate,
    labels,
    minimal_unmet,
    parse,
)

# Real expressions taken from UTS eVOP access-conditions pages, ascending difficulty.
REAL_RULES = {
    "32310": "(1)",
    "32009": "((1) OR (2 AND 3))",
    "32509": "((1 OR 1a OR 1b OR 1c OR 1d OR 1e OR 1f) AND 2)",
    "32146": "((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))",
    "32513": "(((1 OR 1a)) OR ((2 OR 2a) AND (3 OR 3a)) OR (4 AND (5 OR 5a)) OR (6))",
    "32570": "(1 AND 2 AND 3)",
}


def test_single_label_collapses_redundant_parens():
    assert parse("(1)") == Label("1")
    assert parse("((1))") == Label("1")


def test_and_binds_tighter_than_or():
    assert parse("1 OR 2 AND 3") == Or((Label("1"), And((Label("2"), Label("3")))))


def test_alphanumeric_labels():
    assert parse("2 OR 2a OR 2b") == Or((Label("2"), Label("2a"), Label("2b")))


def test_parses_real_nested_rule():
    assert parse(REAL_RULES["32009"]) == Or((Label("1"), And((Label("2"), Label("3")))))


@pytest.mark.parametrize("expr", REAL_RULES.values())
def test_every_real_rule_parses(expr):
    assert parse(expr) is not None


def test_labels_collects_every_leaf():
    assert labels(parse(REAL_RULES["32146"])) == frozenset(
        {"1", "2", "2a", "2b", "3", "4", "5", "6"}
    )


def test_depth_measures_nesting():
    assert depth(parse("(1)")) == 1
    assert depth(parse("1 OR 2 AND 3")) == 3


@pytest.mark.parametrize(
    "bad", ["", "(1", "1)", "1 AND", "AND 1", "1 2", "(1 OR)", "@"]
)
def test_malformed_rules_raise(bad):
    with pytest.raises(RuleSyntaxError):
        parse(bad)


def test_evaluate_simple_conjunction():
    node = parse("1 AND 2")
    assert evaluate(node, {"1": True, "2": True}) is True
    assert evaluate(node, {"1": True, "2": False}) is False


def _reference_eval(expr: str, truth: dict[str, bool]) -> bool:
    """Independent oracle: rewrite the expression into Python and eval it."""
    import re

    py = re.sub(r"\d+[a-z]*", lambda m: str(truth[m.group(0)]), expr)
    py = py.replace("AND", " and ").replace("OR", " or ")
    return bool(eval(py))


@pytest.mark.parametrize("expr", REAL_RULES.values())
def test_agrees_with_truth_table_oracle(expr):
    node = parse(expr)
    names = sorted(labels(node))
    rng = random.Random(0)
    for _ in range(200):
        truth = {n: rng.choice([True, False]) for n in names}
        assert evaluate(node, truth) == _reference_eval(expr, truth)


def test_minimal_unmet_picks_cheapest_or_branch():
    node = parse("(1 AND 2) OR 3")
    truth = {"1": True, "2": False, "3": False}
    assert minimal_unmet(node, truth) == frozenset({"3"})


def test_minimal_unmet_is_empty_when_satisfied():
    node = parse("1 OR 2")
    assert minimal_unmet(node, {"1": True, "2": False}) == frozenset()


def test_minimal_unmet_unions_across_and():
    node = parse("1 AND 2")
    assert minimal_unmet(node, {"1": False, "2": False}) == frozenset({"1", "2"})
