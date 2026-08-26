# Enrolment Dataset and Requisite Rule Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the enrolment case-study data layer — real UTS catalogue and requisite data for course C04296, plus a deterministic parser and evaluator for UTS requisite rule expressions.

**Architecture:** A new top-level package `src/enrolment/` holds everything domain-specific, keeping `src/policy_guard/` free of enrolment symbols so the domain-agnostic claim is provable from the file layout. Within it, parsing is split from resolution: `rules.py` turns a rule expression into an AST with no knowledge of subjects or students, and `requisites.py` resolves AST labels against a student record. Acquisition (`fetch.py`) is separated from extraction (`handbook.py`, `evop.py`) so parsing can be re-run offline against committed raw responses.

**Tech Stack:** Python 3.11+, pydantic v2, pytest, ruff. HTTP via stdlib `urllib.request` — no new dependencies.

**Spec:** `context/2026-08-26-enrolment-dataset-design.md`

## Global Constraints

- Python `>=3.11`; pydantic `>=2.0`; pytest `>=8.0`; ruff `>=0.4` (already in `pyproject.toml`)
- ruff `line-length = 100`
- **No new dependencies.** HTTP uses stdlib `urllib.request`.
- **`src/policy_guard/` must not gain any enrolment-specific symbol.** All work here lands in `src/enrolment/`.
- Tests live flat in `tests/` (matching `tests/test_engine.py`, `tests/test_allowlist.py`), fixtures in `tests/fixtures/`.
- **No network access in any test.** Fetching is tested through an injected transport; extraction is tested against committed fixtures.
- Every persisted record carries `source_url` and `fetched_at`.
- **Unparsed requisite items are never dropped.** They are stored with `kind="unparsed"` and the raw `text`, and counted in the coverage report.
- All student data is synthetic. Only catalogue and requisite data is real.
- Network fetches are serial with a delay between them, and cached to `data/raw/`.

## File Structure

| File | Responsibility |
|---|---|
| `src/enrolment/__init__.py` | package marker |
| `src/enrolment/rules.py` | tokenizer, recursive-descent parser, AST, `evaluate`, `labels`, `depth`, `minimal_unmet` — no domain knowledge |
| `src/enrolment/models.py` | pydantic models shared across the package |
| `src/enrolment/evop.py` | eVOP access-conditions HTML → `SubjectRequisites` |
| `src/enrolment/handbook.py` | handbook `__NEXT_DATA__` JSON → `Subject`, `Course` |
| `src/enrolment/requisites.py` | AST + items + `Student` → `RequisiteResult` |
| `src/enrolment/fetch.py` | HTTP with on-disk raw cache, provenance, politeness delay |
| `src/enrolment/build.py` | CLI orchestrating fetch → extract → write `data/*.json`, plus coverage report |
| `src/enrolment/students.py` | deterministic synthetic student generator |
| `src/enrolment/scenarios.py` | compliance scenario generator, stratified by failure mode and rule depth |

Tests: `tests/test_rules.py`, `tests/test_evop.py`, `tests/test_handbook.py`, `tests/test_requisites.py`, `tests/test_fetch.py`, `tests/test_students.py`, `tests/test_scenarios.py`.

**Deviation from the spec, deliberate:** the spec sketches
`evaluate_requisites(subject_code, student, catalogue, requisites)`. The
catalogue parameter is dropped — completed-subject credit points are carried
on the student's own `completed` records, so the catalogue is not needed to
evaluate a rule. Task 5 uses the three-argument form.

---

### Task 1: Rule expression parser

Pure and domain-free. Built first because it has no dependencies and carries the highest risk.

**Files:**
- Create: `src/enrolment/__init__.py` (empty)
- Create: `src/enrolment/rules.py`
- Test: `tests/test_rules.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Label(name: str)`, `And(parts: tuple[Node, ...])`, `Or(parts: tuple[Node, ...])` — frozen dataclasses
  - `Node = Label | And | Or`
  - `RuleSyntaxError(ValueError)`
  - `parse(text: str) -> Node`
  - `evaluate(node: Node, truth: dict[str, bool]) -> bool`
  - `labels(node: Node) -> frozenset[str]`
  - `depth(node: Node) -> int`
  - `minimal_unmet(node: Node, truth: dict[str, bool]) -> frozenset[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rules.py`:

```python
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
    return bool(eval(py))  # noqa: S307 - test-only oracle over a closed grammar


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/__init__.py` as an empty file.

Create `src/enrolment/rules.py`:

```python
"""Parser and evaluator for UTS requisite rule expressions.

UTS publishes requisites as a boolean expression over labelled items, e.g.
`((1 AND (2 OR 2a OR 2b)) OR (3) OR (4))`, alongside a table mapping each
label to a subject, credit-point threshold, area of study or admission.

This module handles the expression only. It knows nothing about subjects or
students, which is what makes it testable against a plain truth table.

Grammar:
    expr   := term { "OR" term }
    term   := factor { "AND" factor }
    factor := "(" expr ")" | label
    label  := digit+ letter*
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKEN_RE = re.compile(r"\(|\)|AND\b|OR\b|\d+[a-z]*")


class RuleSyntaxError(ValueError):
    """Raised when a rule expression does not match the grammar."""


@dataclass(frozen=True)
class Label:
    name: str


@dataclass(frozen=True)
class And:
    parts: tuple["Node", ...]


@dataclass(frozen=True)
class Or:
    parts: tuple["Node", ...]


Node = Label | And | Or


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        match = TOKEN_RE.match(text, pos)
        if match is None:
            raise RuleSyntaxError(f"unexpected character {text[pos]!r} at position {pos}")
        tokens.append(match.group(0))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise RuleSyntaxError("unexpected end of rule")
        self.pos += 1
        return token

    def expr(self) -> Node:
        parts = [self.term()]
        while self.peek() == "OR":
            self.take()
            parts.append(self.term())
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def term(self) -> Node:
        parts = [self.factor()]
        while self.peek() == "AND":
            self.take()
            parts.append(self.factor())
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def factor(self) -> Node:
        token = self.take()
        if token == "(":
            node = self.expr()
            closing = self.take()
            if closing != ")":
                raise RuleSyntaxError(f"expected ')' but found {closing!r}")
            return node
        if token in ("AND", "OR", ")"):
            raise RuleSyntaxError(f"expected a label or '(' but found {token!r}")
        return Label(token)


def parse(text: str) -> Node:
    tokens = tokenize(text)
    if not tokens:
        raise RuleSyntaxError("empty rule")
    parser = _Parser(tokens)
    node = parser.expr()
    if parser.peek() is not None:
        raise RuleSyntaxError(f"trailing input at token {parser.peek()!r}")
    return node


def evaluate(node: Node, truth: dict[str, bool]) -> bool:
    if isinstance(node, Label):
        return truth.get(node.name, False)
    if isinstance(node, And):
        return all(evaluate(part, truth) for part in node.parts)
    return any(evaluate(part, truth) for part in node.parts)


def labels(node: Node) -> frozenset[str]:
    if isinstance(node, Label):
        return frozenset({node.name})
    return frozenset().union(*(labels(part) for part in node.parts))


def depth(node: Node) -> int:
    """Nesting depth, used to stratify generated scenarios by difficulty."""
    if isinstance(node, Label):
        return 1
    return 1 + max(depth(part) for part in node.parts)


def minimal_unmet(node: Node, truth: dict[str, bool]) -> frozenset[str]:
    """Smallest set of labels that would have to become true to satisfy `node`.

    Exact for the tree-shaped expressions UTS publishes: AND unions its
    children's requirements, OR takes the cheapest branch.
    """
    if isinstance(node, Label):
        return frozenset() if truth.get(node.name, False) else frozenset({node.name})
    if isinstance(node, And):
        return frozenset().union(*(minimal_unmet(part, truth) for part in node.parts))
    return min((minimal_unmet(part, truth) for part in node.parts), key=len)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rules.py -v`
Expected: PASS — all tests green.

Then confirm nothing else broke and lint is clean:

Run: `pytest -q && ruff check src tests`
Expected: all tests pass, ruff reports no issues.

- [ ] **Step 5: Commit**

```bash
git add src/enrolment/__init__.py src/enrolment/rules.py tests/test_rules.py
git commit -m "feat(enrolment): add requisite rule expression parser and evaluator"
```

---

### Task 2: Domain models

**Files:**
- Create: `src/enrolment/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `RequisiteItem`, `SubjectRequisites`, `Subject`, `BlockItem`, `CourseBlock`, `Course`, `CompletedSubject`, `Student`, `ItemOutcome`, `RequisiteResult` — all pydantic v2 `BaseModel`

- [ ] **Step 1: Write the failing test**

Create `tests/test_models.py`:

```python
from enrolment.models import (
    CompletedSubject,
    RequisiteItem,
    Student,
    SubjectRequisites,
)


def test_requisite_item_keeps_raw_text_for_unparsed_kind():
    item = RequisiteItem(label="1", kind="unparsed", text="something we did not recognise")
    assert item.text == "something we did not recognise"
    assert item.subject is None


def test_subject_requisites_defaults_to_no_rules():
    req = SubjectRequisites(
        code="42890", source_url="https://example.test", fetched_at="2026-08-26"
    )
    assert req.requisite_rule is None
    assert req.requisite_items == {}
    assert req.antirequisite_rule is None


def test_student_round_trips_through_json():
    student = Student(
        student_id="S1000001",
        course="C04296",
        admitted_to=["C04296", "Master's Coursework courses owned by FEIT"],
        major="MAJ02093",
        completed=[CompletedSubject(code="32541", session="2025-AUT", credit_points=6)],
        enrolled=["32144"],
    )
    restored = Student.model_validate_json(student.model_dump_json())
    assert restored == student
    assert restored.credit_point_cap_per_session == 24


def test_student_completed_codes_helper():
    student = Student(
        student_id="S1",
        course="C04296",
        completed=[
            CompletedSubject(code="32541", session="2025-AUT", credit_points=6),
            CompletedSubject(code="43030", session="2025-SPR", credit_points=6),
        ],
    )
    assert student.completed_codes() == {"32541", "43030"}
    assert student.completed_credit_points() == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.models'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/models.py`:

```python
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
    children: list["CourseBlock"] = Field(default_factory=list)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/enrolment/models.py tests/test_models.py
git commit -m "feat(enrolment): add domain models for catalogue, requisites and students"
```

---

### Task 3: eVOP access-conditions parser

**Files:**
- Create: `src/enrolment/evop.py`
- Create: `tests/fixtures/evop_31251.html`, `tests/fixtures/evop_32146.html`, `tests/fixtures/evop_32933.html`, `tests/fixtures/evop_42890.html`
- Test: `tests/test_evop.py`

**Interfaces:**
- Consumes: `enrolment.models.RequisiteItem`, `enrolment.models.SubjectRequisites`
- Produces:
  - `flatten(html: str) -> list[str]`
  - `classify_item(label: str, item_type: str, text: str) -> RequisiteItem`
  - `parse_evop(html: str, code: str, source_url: str, fetched_at: str) -> SubjectRequisites`

- [ ] **Step 1: Capture the fixtures**

These four subjects cover the shapes seen in reconnaissance: a simple rule with both tables (31251), a deeply nested rule (32146), mixed academic and admission item types (32933), and a subject with no published requisites (42890).

Run once, outside the test suite:

```bash
mkdir -p tests/fixtures
for code in 31251 32146 32933 42890; do
  curl -sL -A "mcp-policy-guard research (UTS 32933)" \
    "https://studentforms.uts.edu.au/evop/access/search.cfm?subjectcode=${code}" \
    -o "tests/fixtures/evop_${code}.html"
  sleep 2
done
ls -la tests/fixtures/
```

Sanity-check one before continuing:

```bash
grep -o "Requisite rule:[^<]*" tests/fixtures/evop_32146.html
```
Expected: `Requisite rule: ((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))`

If the expression differs, UTS has changed the data since 2026-08-26. Update the expected values in Step 2 to match what the page now publishes, and note the change in the spec.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_evop.py`:

```python
from pathlib import Path

import pytest

from enrolment.evop import classify_item, parse_evop

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://studentforms.uts.edu.au/evop/access/search.cfm?subjectcode="
WHEN = "2026-08-26T00:00:00"


def load(code: str):
    html = (FIXTURES / f"evop_{code}.html").read_text(encoding="utf-8", errors="replace")
    return parse_evop(html, code, URL + code, WHEN)


def test_parses_simple_rule_and_both_tables():
    req = load("31251")
    assert req.requisite_rule == "(1)"
    assert req.antirequisite_rule == "(1 AND 2)"
    assert req.requisite_items["1"].subject == "48024"
    assert req.requisite_items["1"].kind == "subject"
    assert req.requisite_items["1"].type == "academic"
    assert req.antirequisite_items["1"].subject == "31473"
    assert req.antirequisite_items["2"].subject == "32510"


def test_parses_deeply_nested_rule():
    req = load("32146")
    assert req.requisite_rule == "((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))"
    assert req.antirequisite_rule == "(1 AND 2 AND 3)"
    assert set(req.requisite_items) == {"1", "2", "2a", "2b", "3", "4", "5", "6"}
    assert req.requisite_items["1"].subject == "31250"
    assert req.requisite_items["2a"].subject == "43031"


def test_distinguishes_academic_from_admission_items():
    req = load("32933")
    assert req.requisite_items["1"].kind == "subject"
    assert req.requisite_items["1"].subject == "32144"
    assert req.requisite_items["2"].kind == "admission"
    assert req.requisite_items["2"].type == "admission"
    assert "FEIT" in req.requisite_items["2"].admission_descriptor


def test_subject_without_requisites_has_no_rules():
    req = load("42890")
    assert req.requisite_rule is None
    assert req.antirequisite_rule is None
    assert req.requisite_items == {}


def test_provenance_is_recorded():
    req = load("31251")
    assert req.source_url.endswith("31251")
    assert req.fetched_at == WHEN


@pytest.mark.parametrize(
    "text,kind,attr,value",
    [
        ("48024 Programming 2", "subject", "subject", "48024"),
        ("MAJ02902 36cp Information Technology Major MBA", "aos", "aos_code", "MAJ02902"),
        (
            "Admission into C04296 Master of Information Technology (Extension)",
            "admission",
            "course",
            "C04296",
        ),
        (
            "Must have completed at least 48 credit points in 48.0000000000 Credit Points",
            "credit_points",
            "credit_points",
            48,
        ),
    ],
)
def test_classify_item_recognises_each_payload_kind(text, kind, attr, value):
    item = classify_item("1", "academic", text)
    assert item.kind == kind
    assert getattr(item, attr) == value


def test_unrecognised_payload_is_kept_as_unparsed():
    item = classify_item("1", "other", "some wording we have never seen")
    assert item.kind == "unparsed"
    assert item.text == "some wording we have never seen"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_evop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.evop'`

- [ ] **Step 4: Write the implementation**

Create `src/enrolment/evop.py`:

```python
"""Parser for UTS eVOP access-conditions pages.

`studentforms.uts.edu.au/evop/access/search.cfm?subjectcode=<code>` is where
UTS publishes requisites; the course handbook's own requisite fields are
empty. Each page carries up to two tables — anti-requisites and requisites —
each with a boolean rule expression over numbered items.

The page is server-rendered HTML with no stable class names, so parsing works
on the flattened text: every tag becomes a line break, and records are
delimited by lines that are bare item labels.
"""

from __future__ import annotations

import html as html_module
import re

from .models import RequisiteItem, SubjectRequisites

LABEL_RE = re.compile(r"^\d+[a-z]*$")
SUBJECT_RE = re.compile(r"^(\d{5,6})\s+(.+)$")
AOS_RE = re.compile(r"^((?:MAJ|SMJ|CBK|STM)\d+)\s+(.+)$")
ADMISSION_RE = re.compile(r"^Admission\s+(?:into\s+)?(.*)$", re.IGNORECASE)
COURSE_CODE_RE = re.compile(r"\b(C\d{5})\b")
CREDIT_POINTS_RE = re.compile(r"at least\s+(\d+)\s+credit points", re.IGNORECASE)

ANTI_RULE_PREFIX = "Anti-requisite rule:"
RULE_PREFIX = "Requisite rule:"
REQUISITE_HEADING = "Requisite(s)"
END_MARKER = "Note: See also"

TYPE_WORDS = {
    "Academic requisite": "academic",
    "Admission requisite": "admission",
    "Other requisite": "other",
}
HEADER_WORDS = {"Item", "Type", "Details"}


def flatten(html: str) -> list[str]:
    """Reduce the page to a list of non-empty text lines."""
    text = re.sub(r"<script.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html_module.unescape(text)
    return [line.strip() for line in text.split("\n") if line.strip()]


def classify_item(label: str, item_type: str, text: str) -> RequisiteItem:
    """Turn one table row's payload into a typed item, never discarding `text`."""
    admission = ADMISSION_RE.match(text)
    if admission:
        descriptor = admission.group(1).strip()
        course = COURSE_CODE_RE.search(descriptor)
        return RequisiteItem(
            label=label,
            kind="admission",
            type=item_type,
            text=text,
            course=course.group(1) if course else None,
            admission_descriptor=descriptor,
        )

    credit_points = CREDIT_POINTS_RE.search(text)
    if credit_points:
        return RequisiteItem(
            label=label,
            kind="credit_points",
            type=item_type,
            text=text,
            credit_points=int(credit_points.group(1)),
        )

    subject = SUBJECT_RE.match(text)
    if subject:
        return RequisiteItem(
            label=label, kind="subject", type=item_type, text=text, subject=subject.group(1)
        )

    aos = AOS_RE.match(text)
    if aos:
        return RequisiteItem(
            label=label, kind="aos", type=item_type, text=text, aos_code=aos.group(1)
        )

    return RequisiteItem(label=label, kind="unparsed", type=item_type, text=text)


def _parse_table(lines: list[str]) -> dict[str, RequisiteItem]:
    collected: dict[str, dict] = {}
    current: str | None = None
    for line in lines:
        if line in HEADER_WORDS:
            continue
        if LABEL_RE.match(line):
            current = line
            collected[current] = {"type": "unknown", "text": []}
            continue
        if current is None:
            continue
        record = collected[current]
        if not record["text"] and line in TYPE_WORDS:
            record["type"] = TYPE_WORDS[line]
            continue
        record["text"].append(line)
    return {
        label: classify_item(label, record["type"], " ".join(record["text"]).strip())
        for label, record in collected.items()
        if record["text"]
    }


def parse_evop(html: str, code: str, source_url: str, fetched_at: str) -> SubjectRequisites:
    lines = flatten(html)

    anti_rule_idx = next(
        (i for i, line in enumerate(lines) if line.startswith(ANTI_RULE_PREFIX)), None
    )
    rule_idx = next(
        (
            i
            for i, line in enumerate(lines)
            if line.startswith(RULE_PREFIX) and not line.startswith(ANTI_RULE_PREFIX)
        ),
        None,
    )
    end = next((i for i, line in enumerate(lines) if line.startswith(END_MARKER)), len(lines))
    heading_idx = next((i for i, line in enumerate(lines) if line == REQUISITE_HEADING), None)

    result = SubjectRequisites(code=code, source_url=source_url, fetched_at=fetched_at)

    if anti_rule_idx is not None:
        result.antirequisite_rule = lines[anti_rule_idx][len(ANTI_RULE_PREFIX) :].strip()
        anti_end = (
            heading_idx if heading_idx is not None and heading_idx > anti_rule_idx else end
        )
        result.antirequisite_items = _parse_table(lines[anti_rule_idx + 1 : anti_end])

    if rule_idx is not None:
        result.requisite_rule = lines[rule_idx][len(RULE_PREFIX) :].strip()
        result.requisite_items = _parse_table(lines[rule_idx + 1 : end])

    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_evop.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/enrolment/evop.py tests/test_evop.py tests/fixtures/evop_*.html
git commit -m "feat(enrolment): parse requisite rules from eVOP access-conditions pages"
```

---

### Task 4: Handbook extractor

**Files:**
- Create: `src/enrolment/handbook.py`
- Create: `tests/fixtures/handbook_subject_32146.json`, `tests/fixtures/handbook_course_c04296.json`
- Test: `tests/test_handbook.py`

**Interfaces:**
- Consumes: `enrolment.models.Subject`, `Course`, `CourseBlock`, `BlockItem`
- Produces:
  - `extract_page_content(html: str) -> dict`
  - `strip_html(raw: str) -> str`
  - `parse_subject(page_content: dict, source_url: str, fetched_at: str) -> Subject`
  - `parse_course(page_content: dict, source_url: str, fetched_at: str, code: str | None = None) -> Course`
  - `referenced_codes(course: Course, item_type: str) -> list[str]`

- [ ] **Step 1: Capture the fixtures**

The handbook is a Next.js app; all page data sits in a `__NEXT_DATA__` script tag. Committing the full 200KB HTML would be wasteful, so the fixtures store only the extracted `pageContent` object.

Run once, outside the test suite:

```bash
python3 - <<'EOF'
import json, re, time, urllib.request
from pathlib import Path

def page_content(path):
    req = urllib.request.Request(
        f"https://coursehandbook.uts.edu.au{path}",
        headers={"User-Agent": "mcp-policy-guard research (UTS 32933)"},
    )
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    payload = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    ).group(1)
    return json.loads(payload)["props"]["pageProps"]["pageContent"]

Path("tests/fixtures").mkdir(parents=True, exist_ok=True)
for path, name in [
    ("/subject/2026/32146", "handbook_subject_32146.json"),
    ("/course/2026/c04296", "handbook_course_c04296.json"),
]:
    json.dump(page_content(path), open(f"tests/fixtures/{name}", "w"), indent=1)
    print("wrote", name)
    time.sleep(2)
EOF
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_handbook.py`:

```python
import json
from pathlib import Path

import pytest

from enrolment.handbook import (
    extract_page_content,
    parse_course,
    parse_subject,
    referenced_codes,
    strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures"
WHEN = "2026-08-26T00:00:00"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_extract_page_content_reads_next_data_payload():
    html = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"pageContent":{"code":"12345"}}}}'
        "</script></body></html>"
    )
    assert extract_page_content(html) == {"code": "12345"}


def test_extract_page_content_rejects_page_without_payload():
    with pytest.raises(ValueError, match="__NEXT_DATA__"):
        extract_page_content("<html><body>nothing here</body></html>")


def test_strip_html_removes_markup_and_unescapes():
    assert strip_html("<p>Secure &amp; sound</p>") == "Secure & sound"


def test_parse_subject_reads_core_fields():
    subject = parse_subject(
        fixture("handbook_subject_32146.json"), "https://example.test/32146", WHEN
    )
    assert subject.code == "32146"
    assert subject.name == "Data Visualisation and Visual Analytics"
    assert subject.credit_points == 6
    assert "<" not in subject.description
    assert subject.source_url == "https://example.test/32146"


def test_parse_subject_derives_sessions_from_offerings():
    subject = parse_subject(fixture("handbook_subject_32146.json"), "https://example.test", WHEN)
    assert subject.sessions == ["Autumn", "Spring"]


def test_parse_course_reads_structure():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    assert course.code == "C04296"
    assert course.credit_points == 96
    titles = {block.title for block in course.blocks}
    assert {"Core", "Majors", "Sub-Majors"} <= titles


def test_parse_course_keeps_core_subjects_with_credit_points():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    core = next(block for block in course.blocks if block.title == "Core")
    assert core.credit_points == 18
    assert {item.code for item in core.items} == {"32541", "43030", "32144"}
    assert all(item.credit_points == 6 for item in core.items)
    assert all(item.item_type == "subject" for item in core.items)


def test_parse_course_preserves_nesting():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    project = next(block for block in course.blocks if "Project" in block.title)
    assert project.children, "IT Project and Electives should have nested blocks"


def test_referenced_codes_filters_by_item_type():
    course = parse_course(fixture("handbook_course_c04296.json"), "https://example.test", WHEN)
    majors = referenced_codes(course, "major")
    assert "MAJ02093" in majors
    assert all(code.startswith("MAJ") for code in majors)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_handbook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.handbook'`

- [ ] **Step 4: Write the implementation**

Create `src/enrolment/handbook.py`:

```python
"""Extractors for the UTS course handbook.

The handbook is a Next.js application: every page embeds its data as JSON in
a `__NEXT_DATA__` script tag, so extraction is a JSON traversal rather than
HTML scraping. Course structure lives under
`curriculumStructure.container[].relationship[]`, where each relationship
carries an item code, credit points, type and a `/aos/<year>/<CODE>` URL for
majors, sub-majors and elective streams.

Note that the handbook's own requisite fields are empty for every subject —
requisites come from `enrolment.evop` instead.
"""

from __future__ import annotations

import html as html_module
import json
import re

from .models import BlockItem, Course, CourseBlock, Subject

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)


def extract_page_content(html: str) -> dict:
    match = NEXT_DATA_RE.search(html)
    if match is None:
        raise ValueError("page carries no __NEXT_DATA__ payload")
    return json.loads(match.group(1))["props"]["pageProps"]["pageContent"]


def strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html_module.unescape(text)).strip()


def _as_int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_subject(page_content: dict, source_url: str, fetched_at: str) -> Subject:
    sessions = sorted(
        {
            (offering.get("teaching_period") or "").removesuffix(" Session").strip()
            for offering in page_content.get("offering") or []
            if offering.get("teaching_period")
        }
    )
    return Subject(
        code=page_content["code"],
        name=page_content["title"],
        credit_points=_as_int(page_content.get("credit_points")) or 0,
        description=strip_html(page_content.get("description") or ""),
        sessions=[session for session in sessions if session],
        source_url=source_url,
        fetched_at=fetched_at,
    )


def _block(container: dict) -> CourseBlock:
    items = []
    for relationship in container.get("relationship") or []:
        code = relationship.get("academic_item_code")
        if not code:
            continue
        items.append(
            BlockItem(
                code=code,
                name=relationship.get("academic_item_name") or "",
                credit_points=_as_int(relationship.get("academic_item_credit_points")),
                item_type=(relationship.get("academic_item_type") or {}).get("value") or "unknown",
                url=relationship.get("academic_item_url") or None,
                connector=(relationship.get("parent_connector") or {}).get("value") or None,
            )
        )
    return CourseBlock(
        title=container.get("title") or container.get("name") or "",
        description=strip_html(container.get("description") or ""),
        credit_points=_as_int(container.get("credit_points")),
        items=items,
        children=[_block(child) for child in container.get("container") or []],
    )


def parse_course(
    page_content: dict, source_url: str, fetched_at: str, code: str | None = None
) -> Course:
    """Parse a course or area-of-study page.

    `/aos/<year>/<CODE>` pages publish majors, sub-majors and elective streams
    using the same curriculum-structure shape as a course page, but without a
    `code` field — hence the override.
    """
    structure = page_content.get("curriculumStructure") or {}
    return Course(
        code=code or page_content.get("code") or "",
        title=page_content.get("title") or "",
        credit_points=_as_int(page_content.get("credit_points")),
        blocks=[_block(container) for container in structure.get("container") or []],
        source_url=source_url,
        fetched_at=fetched_at,
    )


def referenced_codes(course: Course, item_type: str) -> list[str]:
    """Every code of the given item type anywhere in the course tree, deduplicated."""
    found: list[str] = []

    def walk(block: CourseBlock) -> None:
        for item in block.items:
            if item.item_type == item_type and item.code not in found:
                found.append(item.code)
        for child in block.children:
            walk(child)

    for block in course.blocks:
        walk(block)
    return found
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_handbook.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/enrolment/handbook.py tests/test_handbook.py tests/fixtures/handbook_*.json
git commit -m "feat(enrolment): extract catalogue and course structure from handbook payloads"
```

---

### Task 5: Requisite evaluator

Resolves rule labels against a student record. This is the component the project's central claim rests on: it must be exact where a language model is merely probable.

**Files:**
- Create: `src/enrolment/requisites.py`
- Test: `tests/test_requisites.py`

**Interfaces:**
- Consumes: `enrolment.rules.parse`, `evaluate`, `labels`, `minimal_unmet`; `enrolment.models.*`
- Produces:
  - `resolve_item(item: RequisiteItem, student: Student) -> ItemOutcome`
  - `evaluate_requisites(subject_code: str, student: Student, requisites: dict[str, SubjectRequisites]) -> RequisiteResult`
  - `exceeds_credit_cap(student: Student, subject: Subject, enrolled_points: int | None = None) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_requisites.py`:

```python
import pytest

from enrolment.models import (
    CompletedSubject,
    RequisiteItem,
    Student,
    Subject,
    SubjectRequisites,
)
from enrolment.requisites import evaluate_requisites, exceeds_credit_cap, resolve_item

WHEN = "2026-08-26T00:00:00"


def subject_item(label: str, code: str) -> RequisiteItem:
    return RequisiteItem(
        label=label, kind="subject", type="academic", text=f"{code} Something", subject=code
    )


def requisites(code: str, rule: str, items: dict[str, RequisiteItem], **kwargs):
    return {
        code: SubjectRequisites(
            code=code,
            requisite_rule=rule,
            requisite_items=items,
            source_url="https://example.test",
            fetched_at=WHEN,
            **kwargs,
        )
    }


def student(**kwargs) -> Student:
    defaults = dict(student_id="S1", course="C04296", admitted_to=["C04296"])
    defaults.update(kwargs)
    return Student(**defaults)


def completed(*codes: str) -> list[CompletedSubject]:
    return [CompletedSubject(code=code, session="2025-AUT", credit_points=6) for code in codes]


def test_satisfied_when_required_subject_completed():
    reqs = requisites("32146", "(1)", {"1": subject_item("1", "31250")})
    result = evaluate_requisites("32146", student(completed=completed("31250")), reqs)
    assert result.satisfied is True
    assert result.unmet == []


def test_unsatisfied_reports_minimal_unmet_set():
    reqs = requisites(
        "32146",
        "(1 AND (2 OR 2a))",
        {
            "1": subject_item("1", "31250"),
            "2": subject_item("2", "48024"),
            "2a": subject_item("2a", "43031"),
        },
    )
    result = evaluate_requisites("32146", student(completed=completed("31250")), reqs)
    assert result.satisfied is False
    assert len(result.unmet) == 1
    assert result.unmet[0] in {"2", "2a"}


def test_explanation_names_the_missing_requisite():
    reqs = requisites("32146", "(1)", {"1": subject_item("1", "31250")})
    result = evaluate_requisites("32146", student(), reqs)
    assert "31250" in result.explanation


def test_subject_with_no_requisite_record_is_satisfied():
    result = evaluate_requisites("42890", student(), {})
    assert result.satisfied is True


def test_antirequisite_blocks_enrolment():
    reqs = requisites(
        "31251",
        "(1)",
        {"1": subject_item("1", "48024")},
        antirequisite_rule="(1 AND 2)",
        antirequisite_items={
            "1": subject_item("1", "31473"),
            "2": subject_item("2", "32510"),
        },
    )
    blocked = evaluate_requisites(
        "31251", student(completed=completed("48024", "31473", "32510")), reqs
    )
    assert blocked.satisfied is False
    assert blocked.blocked_by_antirequisite is True

    allowed = evaluate_requisites("31251", student(completed=completed("48024", "31473")), reqs)
    assert allowed.satisfied is True
    assert allowed.blocked_by_antirequisite is False


def test_credit_point_requisite_sums_completed_points():
    item = RequisiteItem(
        label="1",
        kind="credit_points",
        type="academic",
        text="Must have completed at least 48 credit points",
        credit_points=48,
    )
    reqs = requisites("32558", "(1)", {"1": item})
    eight = student(completed=completed(*[f"3200{n}" for n in range(8)]))
    assert evaluate_requisites("32558", eight, reqs).satisfied is True
    four = student(completed=completed("32001", "32002", "32003", "32004"))
    assert evaluate_requisites("32558", four, reqs).satisfied is False


def test_admission_requisite_matches_course_code():
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="Admission into C04296 Master of Information Technology (Extension)",
        course="C04296",
        admission_descriptor="C04296 Master of Information Technology (Extension)",
    )
    reqs = requisites("32531", "(1)", {"1": item})
    assert evaluate_requisites("32531", student(admitted_to=["C04296"]), reqs).satisfied is True
    assert evaluate_requisites("32531", student(admitted_to=["C04405"]), reqs).satisfied is False


def test_admission_requisite_matches_free_text_descriptor():
    item = RequisiteItem(
        label="1",
        kind="admission",
        type="admission",
        text="Admission into Master's Coursework courses owned by FEIT",
        admission_descriptor="Master's Coursework courses owned by FEIT",
    )
    reqs = requisites("32933", "(1)", {"1": item})
    enrolled = student(admitted_to=["Master's Coursework courses owned by FEIT"])
    assert evaluate_requisites("32933", enrolled, reqs).satisfied is True


def test_area_of_study_requisite_matches_major():
    item = RequisiteItem(
        label="1",
        kind="aos",
        type="academic",
        text="MAJ02902 36cp Information Technology Major MBA",
        aos_code="MAJ02902",
    )
    reqs = requisites("32531", "(1)", {"1": item})
    assert evaluate_requisites("32531", student(major="MAJ02902"), reqs).satisfied is True
    assert evaluate_requisites("32531", student(major="MAJ02093"), reqs).satisfied is False


def test_unparsed_item_is_reported_not_silently_passed():
    item = RequisiteItem(label="1", kind="unparsed", type="other", text="wording we cannot read")
    reqs = requisites("99999", "(1)", {"1": item})
    result = evaluate_requisites("99999", student(), reqs)
    assert result.satisfied is False
    assert result.unresolved == ["1"]


def test_label_missing_from_item_table_is_reported_as_unresolved():
    reqs = requisites("99999", "(1 AND 2)", {"1": subject_item("1", "31250")})
    result = evaluate_requisites("99999", student(completed=completed("31250")), reqs)
    assert result.satisfied is False
    assert "2" in result.unresolved


def test_resolve_item_explains_itself():
    outcome = resolve_item(subject_item("1", "31250"), student())
    assert outcome.satisfied is False
    assert "31250" in outcome.reason


@pytest.mark.parametrize(
    "already,points,expected", [(18, 6, False), (24, 6, True), (0, 6, False)]
)
def test_credit_cap(already, points, expected):
    enrolled = student(credit_point_cap_per_session=24)
    enrolled.enrolled = []
    subject = Subject(
        code="32146",
        name="X",
        credit_points=points,
        source_url="https://example.test",
        fetched_at=WHEN,
    )
    enrolled_points = already
    enrolled.completed = []
    assert (
        exceeds_credit_cap(enrolled, subject, enrolled_points=enrolled_points) is expected
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_requisites.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.requisites'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/requisites.py`:

```python
"""Deterministic evaluation of UTS enrolment rules against a student record.

`enrolment.rules` handles the boolean expression; this module resolves each
label in that expression against what the student has actually completed,
what they are admitted to, and which area of study they are in.

Two deliberate choices, both for research validity:

- An item whose payload could not be parsed, or a label with no row in the
  published table, never counts as satisfied. It is recorded in
  `RequisiteResult.unresolved` so the evaluation harness can exclude those
  cases rather than silently scoring them as violations.
- Anti-requisite rules are read literally: the student is blocked only if the
  published expression evaluates true. For `(1 AND 2)` that means holding
  both listed subjects. See the open questions in the design spec.
"""

from __future__ import annotations

import re

from .models import (
    ItemOutcome,
    RequisiteItem,
    RequisiteResult,
    Student,
    Subject,
    SubjectRequisites,
)
from .rules import evaluate, labels, minimal_unmet, parse


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def resolve_item(item: RequisiteItem, student: Student) -> ItemOutcome:
    """Decide whether one requisite item is satisfied by this student."""
    if item.kind == "subject":
        satisfied = item.subject in student.completed_codes()
        return ItemOutcome(
            label=item.label,
            satisfied=satisfied,
            reason=f"{item.subject} {'completed' if satisfied else 'not completed'}",
        )

    if item.kind == "credit_points":
        earned = student.completed_credit_points()
        required = item.credit_points or 0
        return ItemOutcome(
            label=item.label,
            satisfied=earned >= required,
            reason=f"{earned} of {required} required credit points completed",
        )

    if item.kind == "aos":
        held = {student.major, student.sub_major}
        satisfied = item.aos_code in held
        return ItemOutcome(
            label=item.label,
            satisfied=satisfied,
            reason=f"{item.aos_code} {'is' if satisfied else 'is not'} the student's area of study",
        )

    if item.kind == "admission":
        admitted = {_normalise(entry) for entry in student.admitted_to}
        satisfied = (item.course is not None and item.course in student.admitted_to) or (
            _normalise(item.admission_descriptor or "") in admitted
        )
        return ItemOutcome(
            label=item.label,
            satisfied=satisfied,
            reason=f"admission to '{item.admission_descriptor}' "
            f"{'held' if satisfied else 'not held'}",
        )

    return ItemOutcome(
        label=item.label,
        satisfied=False,
        reason=f"requisite could not be interpreted: {item.text}",
    )


def _explain(subject_code: str, unmet: list[str], items: dict[str, RequisiteItem]) -> str:
    if not unmet:
        return f"All requisites for {subject_code} are met."
    described = [items[label].text if label in items else f"requisite {label}" for label in unmet]
    return f"{subject_code} requires: " + "; ".join(described) + "."


def evaluate_requisites(
    subject_code: str,
    student: Student,
    requisites: dict[str, SubjectRequisites],
) -> RequisiteResult:
    record = requisites.get(subject_code)
    if record is None:
        return RequisiteResult(
            subject_code=subject_code,
            satisfied=True,
            explanation=f"No requisites are published for {subject_code}.",
        )

    if record.antirequisite_rule:
        outcomes = {
            label: resolve_item(item, student)
            for label, item in record.antirequisite_items.items()
        }
        truth = {label: outcome.satisfied for label, outcome in outcomes.items()}
        if evaluate(parse(record.antirequisite_rule), truth):
            held = [
                record.antirequisite_items[label].text for label, ok in truth.items() if ok
            ]
            return RequisiteResult(
                subject_code=subject_code,
                satisfied=False,
                rule=record.antirequisite_rule,
                items=outcomes,
                blocked_by_antirequisite=True,
                explanation=(
                    f"{subject_code} is blocked by an anti-requisite: the student has "
                    f"already completed {', '.join(held)}."
                ),
            )

    if not record.requisite_rule:
        return RequisiteResult(
            subject_code=subject_code,
            satisfied=True,
            explanation=f"No requisites are published for {subject_code}.",
        )

    node = parse(record.requisite_rule)
    outcomes = {
        label: resolve_item(item, student) for label, item in record.requisite_items.items()
    }
    truth = {label: outcome.satisfied for label, outcome in outcomes.items()}

    unresolved = sorted(
        label
        for label in labels(node)
        if label not in record.requisite_items or record.requisite_items[label].kind == "unparsed"
    )

    satisfied = evaluate(node, truth)
    unmet = sorted(minimal_unmet(node, truth)) if not satisfied else []

    return RequisiteResult(
        subject_code=subject_code,
        satisfied=satisfied,
        rule=record.requisite_rule,
        items=outcomes,
        unmet=unmet,
        unresolved=unresolved,
        explanation=_explain(subject_code, unmet, record.requisite_items),
    )


def exceeds_credit_cap(
    student: Student, subject: Subject, enrolled_points: int | None = None
) -> bool:
    """Whether adding `subject` would push the student past their session cap.

    `enrolled_points` is the credit points already taken this session. Callers
    that track a session load pass it explicitly; it defaults to zero.
    """
    current = enrolled_points or 0
    return current + subject.credit_points > student.credit_point_cap_per_session
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_requisites.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/enrolment/requisites.py tests/test_requisites.py
git commit -m "feat(enrolment): evaluate requisite rules against student records"
```

---

### Task 6: Fetcher with cache and provenance

**Files:**
- Create: `src/enrolment/fetch.py`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `FetchResult` — frozen dataclass with `url`, `name`, `body`, `fetched_at`, `from_cache`
  - `Fetcher(cache_dir: Path, delay: float = 2.0, transport: Callable[[str], str] | None = None)`
  - `Fetcher.get(url: str, name: str) -> FetchResult`
  - `USER_AGENT: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fetch.py`:

```python
import json

from enrolment.fetch import Fetcher


class FakeTransport:
    def __init__(self, body: str = "<html>hello</html>"):
        self.body = body
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        return self.body


def test_first_get_hits_the_network(tmp_path):
    transport = FakeTransport()
    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=transport)
    result = fetcher.get("https://example.test/a", "a.html")
    assert result.body == "<html>hello</html>"
    assert result.from_cache is False
    assert transport.calls == ["https://example.test/a"]


def test_second_get_is_served_from_cache(tmp_path):
    transport = FakeTransport()
    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=transport)
    fetcher.get("https://example.test/a", "a.html")
    again = fetcher.get("https://example.test/a", "a.html")
    assert again.from_cache is True
    assert len(transport.calls) == 1


def test_cache_survives_a_new_fetcher_instance(tmp_path):
    Fetcher(cache_dir=tmp_path, delay=0, transport=FakeTransport()).get(
        "https://example.test/a", "a.html"
    )
    second_transport = FakeTransport()
    result = Fetcher(cache_dir=tmp_path, delay=0, transport=second_transport).get(
        "https://example.test/a", "a.html"
    )
    assert result.from_cache is True
    assert second_transport.calls == []


def test_provenance_is_written_alongside_the_body(tmp_path):
    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=FakeTransport())
    result = fetcher.get("https://example.test/a", "a.html")
    meta = json.loads((tmp_path / "a.html.meta.json").read_text())
    assert meta["url"] == "https://example.test/a"
    assert meta["fetched_at"] == result.fetched_at
    assert (tmp_path / "a.html").read_text() == "<html>hello</html>"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.fetch'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/fetch.py`:

```python
"""Polite, cached HTTP fetching for the enrolment dataset.

Every response is written to disk with its URL and timestamp, and the cache is
committed to the repository, so experiments never depend on a live UTS server
and the dataset's provenance stays auditable.

Requests are serial with a delay between them. `coursehandbook.uts.edu.au`
publishes a blanket `Disallow: /` in robots.txt; the fetch is deliberately
bounded to one course and run once. See the design spec for the access
question that should be confirmed with the supervisor.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "mcp-policy-guard research (UTS 32933)"


@dataclass(frozen=True)
class FetchResult:
    url: str
    name: str
    body: str
    fetched_at: str
    from_cache: bool


def _urllib_transport(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        delay: float = 2.0,
        transport: Callable[[str], str] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.delay = delay
        self.transport = transport or _urllib_transport

    def get(self, url: str, name: str) -> FetchResult:
        body_path = self.cache_dir / name
        meta_path = self.cache_dir / f"{name}.meta.json"

        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return FetchResult(
                url=meta["url"],
                name=name,
                body=body_path.read_text(encoding="utf-8"),
                fetched_at=meta["fetched_at"],
                from_cache=True,
            )

        body = self.transport(url)
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body_path.write_text(body, encoding="utf-8")
        meta_path.write_text(
            json.dumps({"url": url, "fetched_at": fetched_at}, indent=1), encoding="utf-8"
        )
        if self.delay:
            time.sleep(self.delay)
        return FetchResult(
            url=url, name=name, body=body, fetched_at=fetched_at, from_cache=False
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/enrolment/fetch.py tests/test_fetch.py
git commit -m "feat(enrolment): add cached, provenance-recording fetcher"
```

---

### Task 7: Build pipeline and coverage report

Wires the previous tasks into a runnable dataset build. Completing this task completes the thin slice.

**Files:**
- Create: `src/enrolment/build.py`
- Test: `tests/test_build.py`
- Modify: `.gitignore` (ensure `data/` is **not** ignored — the dataset is committed deliberately)

**Interfaces:**
- Consumes: `enrolment.fetch.Fetcher`, `enrolment.handbook.*`, `enrolment.evop.parse_evop`, `enrolment.models.*`
- Produces:
  - `COURSE_CODE`, `YEAR`, `CORE_CODES`, `THIN_MAJOR`
  - `handbook_course_url(code) -> str`, `handbook_aos_url(code) -> str`, `handbook_subject_url(code) -> str`, `evop_url(code) -> str`
  - `CoverageReport` — dataclass with `subjects`, `with_requisite_rules`, `without_requisite_rules`, `unparsed_items`, `external_subject_references`, `clean` property, `render() -> str`
  - `coverage_report(catalogue, requisites) -> CoverageReport`
  - `subject_codes_for_scope(course, aos_courses, scope) -> list[str]`
  - `build(data_dir, fetcher, scope="thin", codes=None) -> CoverageReport`
  - `main(argv=None) -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build.py`:

```python
import json
from pathlib import Path

from enrolment.build import (
    CoverageReport,
    build,
    coverage_report,
    evop_url,
    handbook_subject_url,
    subject_codes_for_scope,
)
from enrolment.models import (
    BlockItem,
    Course,
    CourseBlock,
    RequisiteItem,
    Subject,
    SubjectRequisites,
)

FIXTURES = Path(__file__).parent / "fixtures"
WHEN = "2026-08-26T00:00:00"


def wrap(page_content: dict) -> str:
    payload = json.dumps({"props": {"pageProps": {"pageContent": page_content}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


class FakeFetcher:
    """Serves committed fixtures by cache name; fails loudly on anything else."""

    def __init__(self):
        course = json.loads((FIXTURES / "handbook_course_c04296.json").read_text())
        subject = json.loads((FIXTURES / "handbook_subject_32146.json").read_text())
        self.bodies = {
            "handbook_course_c04296.html": wrap(course),
            "handbook_subject_32146.html": wrap(subject),
            "evop_32146.html": (FIXTURES / "evop_32146.html").read_text(
                encoding="utf-8", errors="replace"
            ),
        }
        self.requested: list[str] = []

    def get(self, url: str, name: str):
        from enrolment.fetch import FetchResult

        self.requested.append(name)
        if name not in self.bodies:
            raise AssertionError(f"unexpected fetch: {name}")
        return FetchResult(
            url=url, name=name, body=self.bodies[name], fetched_at=WHEN, from_cache=True
        )


def test_url_builders():
    assert handbook_subject_url("32146").endswith("/subject/2026/32146")
    assert evop_url("32146").endswith("subjectcode=32146")


def test_thin_scope_is_core_plus_the_named_major():
    course = Course(
        code="C04296",
        title="MIT (Extension)",
        blocks=[
            CourseBlock(
                title="Core",
                items=[
                    BlockItem(code="32541", name="Project Management", item_type="subject"),
                    BlockItem(code="43030", name="Professional Practice", item_type="subject"),
                    BlockItem(code="32144", name="Tech Research Prep", item_type="subject"),
                ],
            )
        ],
        source_url="https://example.test",
        fetched_at=WHEN,
    )
    cyber = Course(
        code="MAJ02093",
        title="Cybersecurity",
        blocks=[
            CourseBlock(
                title="Choose",
                items=[BlockItem(code="32309", name="Digital Forensics", item_type="subject")],
            )
        ],
        source_url="https://example.test",
        fetched_at=WHEN,
    )
    codes = subject_codes_for_scope(course, {"MAJ02093": cyber}, "thin")
    assert codes == ["32541", "43030", "32144", "32309"]


def test_coverage_report_counts_rules_and_unparsed_items():
    catalogue = {
        "32146": Subject(
            code="32146", name="X", credit_points=6, source_url="u", fetched_at=WHEN
        )
    }
    requisites = {
        "32146": SubjectRequisites(
            code="32146",
            requisite_rule="(1)",
            requisite_items={
                "1": RequisiteItem(label="1", kind="unparsed", text="mystery wording")
            },
            source_url="u",
            fetched_at=WHEN,
        )
    }
    report = coverage_report(catalogue, requisites)
    assert report.subjects == 1
    assert report.with_requisite_rules == 1
    assert report.unparsed_items == [("32146", "1", "mystery wording")]
    assert report.clean is False
    assert "mystery wording" in report.render()


def test_clean_report_has_no_unparsed_items():
    report = CoverageReport(
        subjects=3,
        with_requisite_rules=2,
        without_requisite_rules=1,
        unparsed_items=[],
        external_subject_references=["48024"],
    )
    assert report.clean is True


def test_build_writes_all_three_files(tmp_path):
    fetcher = FakeFetcher()
    report = build(tmp_path, fetcher, scope="thin", codes=["32146"])

    catalogue = json.loads((tmp_path / "catalogue.json").read_text())
    requisites = json.loads((tmp_path / "requisites.json").read_text())
    course = json.loads((tmp_path / "course.json").read_text())

    assert catalogue["32146"]["name"] == "Data Visualisation and Visual Analytics"
    assert requisites["32146"]["requisite_rule"].startswith("((1 AND")
    assert course["code"] == "C04296"
    assert report.subjects == 1
    assert report.clean is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.build'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/build.py`:

```python
"""Build the enrolment dataset from UTS sources.

Run once, commit the result. Experiments read `data/*.json` and never touch
the network.

    python -m enrolment.build --scope thin
    python -m enrolment.build --scope full --fail-on-unparsed

The thin scope is the core subjects plus the Cybersecurity major, and exists
to prove the pipeline before widening to the whole course.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .evop import parse_evop
from .handbook import extract_page_content, parse_course, parse_subject, referenced_codes
from .models import Course, Subject, SubjectRequisites

YEAR = "2026"
COURSE_CODE = "C04296"
CORE_CODES = ["32541", "43030", "32144"]
THIN_MAJOR = "MAJ02093"

HANDBOOK = "https://coursehandbook.uts.edu.au"
EVOP = "https://studentforms.uts.edu.au/evop/access/search.cfm"


def handbook_course_url(code: str = COURSE_CODE) -> str:
    return f"{HANDBOOK}/course/{YEAR}/{code.lower()}"


def handbook_aos_url(code: str) -> str:
    return f"{HANDBOOK}/aos/{YEAR}/{code}"


def handbook_subject_url(code: str) -> str:
    return f"{HANDBOOK}/subject/{YEAR}/{code}"


def evop_url(code: str) -> str:
    return f"{EVOP}?subjectcode={code}"


@dataclass
class CoverageReport:
    subjects: int
    with_requisite_rules: int
    without_requisite_rules: int
    unparsed_items: list[tuple[str, str, str]] = field(default_factory=list)
    external_subject_references: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.unparsed_items

    def render(self) -> str:
        lines = [
            f"subjects:                    {self.subjects}",
            f"with requisite rules:        {self.with_requisite_rules}",
            f"without requisite rules:     {self.without_requisite_rules}",
            f"unparsed requisite items:    {len(self.unparsed_items)}",
            f"references outside C04296:   {len(self.external_subject_references)}",
        ]
        for code, label, text in self.unparsed_items:
            lines.append(f"  UNPARSED {code} item {label}: {text}")
        return "\n".join(lines)


def coverage_report(
    catalogue: dict[str, Subject], requisites: dict[str, SubjectRequisites]
) -> CoverageReport:
    unparsed: list[tuple[str, str, str]] = []
    external: list[str] = []
    with_rules = 0
    for code, record in requisites.items():
        if record.requisite_rule:
            with_rules += 1
        for table in (record.requisite_items, record.antirequisite_items):
            for label, item in table.items():
                if item.kind == "unparsed":
                    unparsed.append((code, label, item.text))
                if (
                    item.kind == "subject"
                    and item.subject
                    and item.subject not in catalogue
                    and item.subject not in external
                ):
                    external.append(item.subject)
    return CoverageReport(
        subjects=len(catalogue),
        with_requisite_rules=with_rules,
        without_requisite_rules=len(requisites) - with_rules,
        unparsed_items=unparsed,
        external_subject_references=external,
    )


def _subject_codes(course: Course) -> list[str]:
    return referenced_codes(course, "subject")


def subject_codes_for_scope(
    course: Course, aos_courses: dict[str, Course], scope: str
) -> list[str]:
    codes = list(CORE_CODES) if scope == "thin" else _subject_codes(course)
    for aos in aos_courses.values():
        for code in _subject_codes(aos):
            if code not in codes:
                codes.append(code)
    return codes


def _load_course(fetcher, code: str, url: str, name: str) -> Course:
    result = fetcher.get(url, name)
    return parse_course(
        extract_page_content(result.body), result.url, result.fetched_at, code=code
    )


def build(
    data_dir, fetcher, scope: str = "thin", codes: list[str] | None = None
) -> CoverageReport:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    course = _load_course(
        fetcher, COURSE_CODE, handbook_course_url(), f"handbook_course_{COURSE_CODE.lower()}.html"
    )

    if codes is None:
        wanted = (
            [THIN_MAJOR]
            if scope == "thin"
            else referenced_codes(course, "major")
            + referenced_codes(course, "sub_major")
            + referenced_codes(course, "stream")
        )
        aos_courses = {
            aos: _load_course(fetcher, aos, handbook_aos_url(aos), f"handbook_aos_{aos}.html")
            for aos in wanted
        }
        codes = subject_codes_for_scope(course, aos_courses, scope)

    catalogue: dict[str, Subject] = {}
    requisites: dict[str, SubjectRequisites] = {}
    for code in codes:
        page = fetcher.get(handbook_subject_url(code), f"handbook_subject_{code}.html")
        catalogue[code] = parse_subject(
            extract_page_content(page.body), page.url, page.fetched_at
        )
        access = fetcher.get(evop_url(code), f"evop_{code}.html")
        requisites[code] = parse_evop(access.body, code, access.url, access.fetched_at)

    _write(data_dir / "course.json", course.model_dump())
    _write(data_dir / "catalogue.json", {c: s.model_dump() for c, s in catalogue.items()})
    _write(data_dir / "requisites.json", {c: r.model_dump() for c, r in requisites.items()})

    return coverage_report(catalogue, requisites)


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the enrolment dataset from UTS sources.")
    parser.add_argument("--scope", choices=["thin", "full"], default="thin")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument(
        "--fail-on-unparsed",
        action="store_true",
        help="exit non-zero if any requisite item could not be interpreted",
    )
    args = parser.parse_args(argv)

    from .fetch import Fetcher

    fetcher = Fetcher(cache_dir=Path(args.data_dir) / "raw", delay=args.delay)
    report = build(args.data_dir, fetcher, scope=args.scope)
    print(report.render())
    return 1 if args.fail_on_unparsed and not report.clean else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_build.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Confirm `data/` is committed, not ignored**

Run: `git check-ignore -v data 2>/dev/null || echo "data/ is not ignored"`
Expected: `data/ is not ignored`. If a rule matches, remove or negate it in `.gitignore` — the dataset and its raw cache are committed on purpose so experiments are reproducible.

- [ ] **Step 6: Run the real thin-slice build**

This is the only step in the plan that touches the network. It is bounded to the core subjects plus the Cybersecurity major, roughly 30 subjects, two requests each, two seconds apart.

Run: `python -m enrolment.build --scope thin --data-dir data`
Expected: a coverage report printed, with `unparsed requisite items: 0`.

If any items are unparsed, do **not** widen to `--scope full`. Add a case to `classify_item` in `src/enrolment/evop.py`, add a test for it in `tests/test_evop.py`, and re-run. The raw responses are cached under `data/raw/`, so re-running costs nothing.

- [ ] **Step 7: Commit**

```bash
git add src/enrolment/build.py tests/test_build.py data/
git commit -m "feat(enrolment): add dataset build pipeline and coverage report"
```

---

## CHECKPOINT — thin slice complete

Tasks 1–7 are the thin slice from the design spec. Before continuing:

- `pytest -q` is green and `ruff check src tests` is clean
- `data/catalogue.json`, `data/requisites.json`, `data/course.json` exist and are committed
- The coverage report shows zero unparsed items
- `src/policy_guard/` contains no enrolment-specific symbol — verify with:
  `grep -rniE "enrol|subject|prerequisit|requisite|student" src/policy_guard/ || echo "clean"`
  (Existing docstrings mention `drop` and `search_subjects` as illustrative examples; code symbols are what matter.)

**Stop here and confirm with the supervisor before running `--scope full`.** The design spec records the robots.txt question; widening the fetch is the point at which it needs an answer.

---

### Task 8: Synthetic student generator

**Files:**
- Create: `src/enrolment/students.py`
- Test: `tests/test_students.py`

**Interfaces:**
- Consumes: `enrolment.models.Student`, `CompletedSubject`, `Subject`
- Produces:
  - `FEIT_DESCRIPTOR: str`
  - `generate_students(catalogue, count=20, seed=0, course="C04296", major=None) -> list[Student]`
  - `write_students(path, students) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_students.py`:

```python
import json

from enrolment.models import Subject
from enrolment.students import FEIT_DESCRIPTOR, generate_students, write_students

WHEN = "2026-08-26T00:00:00"


def catalogue(n: int = 10) -> dict[str, Subject]:
    return {
        f"3200{i}": Subject(
            code=f"3200{i}",
            name=f"Subject {i}",
            credit_points=6,
            source_url="https://example.test",
            fetched_at=WHEN,
        )
        for i in range(n)
    }


def test_generates_the_requested_count():
    students = generate_students(catalogue(), count=5, seed=0)
    assert len(students) == 5


def test_student_ids_are_unique():
    students = generate_students(catalogue(), count=20, seed=0)
    assert len({s.student_id for s in students}) == 20


def test_generation_is_deterministic_for_a_seed():
    first = generate_students(catalogue(), count=5, seed=7)
    second = generate_students(catalogue(), count=5, seed=7)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]


def test_different_seeds_give_different_cohorts():
    first = generate_students(catalogue(), count=5, seed=1)
    second = generate_students(catalogue(), count=5, seed=2)
    assert [s.model_dump() for s in first] != [s.model_dump() for s in second]


def test_completed_subjects_come_from_the_catalogue():
    subjects = catalogue()
    for student in generate_students(subjects, count=10, seed=0):
        assert student.completed_codes() <= set(subjects)


def test_students_carry_both_admission_forms():
    student = generate_students(catalogue(), count=1, seed=0)[0]
    assert "C04296" in student.admitted_to
    assert FEIT_DESCRIPTOR in student.admitted_to


def test_write_students_round_trips(tmp_path):
    students = generate_students(catalogue(), count=3, seed=0)
    path = tmp_path / "students.json"
    write_students(path, students)
    loaded = json.loads(path.read_text())
    assert set(loaded) == {s.student_id for s in students}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_students.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.students'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/students.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_students.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Generate and commit the cohort**

```bash
python3 -c "
import json
from pathlib import Path
from enrolment.models import Subject
from enrolment.students import generate_students, write_students

catalogue = {c: Subject.model_validate(s) for c, s in json.loads(Path('data/catalogue.json').read_text()).items()}
write_students('data/students.json', generate_students(catalogue, count=50, seed=0, major='MAJ02093'))
print('wrote', len(json.loads(Path('data/students.json').read_text())), 'students')
"
```
Expected: `wrote 50 students`

- [ ] **Step 6: Commit**

```bash
git add src/enrolment/students.py tests/test_students.py data/students.json
git commit -m "feat(enrolment): add seeded synthetic student generator"
```

---

### Task 9: Compliance scenario generator

Turns the real rule graph into a benign non-compliance suite, replacing the spec's original 10–15 hand-written scenarios.

**Files:**
- Create: `src/enrolment/scenarios.py`
- Test: `tests/test_scenarios.py`

**Interfaces:**
- Consumes: `enrolment.requisites.evaluate_requisites`, `enrolment.rules.parse`, `depth`, `enrolment.models.*`
- Produces:
  - `ComplianceScenario` — pydantic model with `scenario_id`, `student_id`, `subject_code`, `failure_mode`, `rule_depth`, `expected_satisfied`, `explanation`
  - `FAILURE_MODES: tuple[str, ...]`
  - `classify_failure(result, requisites_record) -> str`
  - `generate_scenarios(students, catalogue, requisites) -> list[ComplianceScenario]`
  - `write_jsonl(path, scenarios) -> None`
  - `summarise(scenarios) -> dict[str, int]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scenarios.py`:

```python
from enrolment.models import (
    CompletedSubject,
    RequisiteItem,
    Student,
    Subject,
    SubjectRequisites,
)
from enrolment.scenarios import (
    generate_scenarios,
    summarise,
    write_jsonl,
)

WHEN = "2026-08-26T00:00:00"


def subject(code: str) -> Subject:
    return Subject(
        code=code, name=f"Subject {code}", credit_points=6, source_url="u", fetched_at=WHEN
    )


CATALOGUE = {code: subject(code) for code in ["32146", "42890", "31251"]}

REQUISITES = {
    "32146": SubjectRequisites(
        code="32146",
        requisite_rule="(1 AND (2 OR 2a))",
        requisite_items={
            "1": RequisiteItem(
                label="1", kind="subject", type="academic", text="31250 Intro", subject="31250"
            ),
            "2": RequisiteItem(
                label="2", kind="subject", type="academic", text="48024 Prog 2", subject="48024"
            ),
            "2a": RequisiteItem(
                label="2a", kind="subject", type="academic", text="43031 Python", subject="43031"
            ),
        },
        source_url="u",
        fetched_at=WHEN,
    ),
    "42890": SubjectRequisites(code="42890", source_url="u", fetched_at=WHEN),
    "31251": SubjectRequisites(
        code="31251",
        antirequisite_rule="(1)",
        antirequisite_items={
            "1": RequisiteItem(
                label="1", kind="subject", type="academic", text="31473 DSPP", subject="31473"
            )
        },
        source_url="u",
        fetched_at=WHEN,
    ),
}


def student(student_id: str, *completed: str) -> Student:
    return Student(
        student_id=student_id,
        course="C04296",
        admitted_to=["C04296"],
        completed=[
            CompletedSubject(code=code, session="2025-AUT", credit_points=6)
            for code in completed
        ],
    )


def test_generates_one_scenario_per_student_subject_pair():
    students = [student("S1"), student("S2", "31250", "48024")]
    scenarios = generate_scenarios(students, CATALOGUE, REQUISITES)
    assert len(scenarios) == len(students) * len(CATALOGUE)


def test_expected_satisfied_matches_the_evaluator():
    met = student("S1", "31250", "48024")
    scenarios = generate_scenarios([met], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["32146"].expected_satisfied is True
    assert by_code["42890"].expected_satisfied is True


def test_unmet_academic_requisite_is_labelled():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["32146"].failure_mode == "unmet_academic"
    assert by_code["32146"].expected_satisfied is False


def test_antirequisite_violation_is_labelled():
    scenarios = generate_scenarios([student("S1", "31473")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["31251"].failure_mode == "antirequisite"


def test_compliant_cases_are_labelled():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["42890"].failure_mode == "compliant"


def test_rule_depth_is_recorded_for_stratification():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    by_code = {s.subject_code: s for s in scenarios}
    assert by_code["32146"].rule_depth >= 2
    assert by_code["42890"].rule_depth == 0


def test_scenario_ids_are_unique():
    students = [student("S1"), student("S2", "31250")]
    scenarios = generate_scenarios(students, CATALOGUE, REQUISITES)
    assert len({s.scenario_id for s in scenarios}) == len(scenarios)


def test_summarise_counts_by_failure_mode():
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    counts = summarise(scenarios)
    assert counts["unmet_academic"] == 1
    assert counts["compliant"] >= 1


def test_write_jsonl_emits_one_object_per_line(tmp_path):
    scenarios = generate_scenarios([student("S1")], CATALOGUE, REQUISITES)
    path = tmp_path / "compliance.jsonl"
    write_jsonl(path, scenarios)
    lines = path.read_text().strip().split("\n")
    assert len(lines) == len(scenarios)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scenarios.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'enrolment.scenarios'`

- [ ] **Step 3: Write the implementation**

Create `src/enrolment/scenarios.py`:

```python
"""Generate the benign non-compliance suite from the real requisite graph.

The design spec replaces hand-written non-compliance scenarios with generated
ones for three reasons: hundreds of cases instead of fifteen, no author bias
in choosing which rules to test, and a difficulty gradient that comes free
from how deeply nested each published rule happens to be.

Each scenario records the exact expected answer, computed by
`enrolment.requisites`. That is the ground truth an agent's behaviour is
scored against — final state, not model output.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from .models import Student, Subject, SubjectRequisites
from .requisites import evaluate_requisites
from .rules import depth, parse

FAILURE_MODES = (
    "compliant",
    "unmet_academic",
    "unmet_admission",
    "unmet_credit_points",
    "antirequisite",
    "unresolved",
)


class ComplianceScenario(BaseModel):
    scenario_id: str
    student_id: str
    subject_code: str
    failure_mode: str
    rule_depth: int
    expected_satisfied: bool
    explanation: str


def classify_failure(result, record: SubjectRequisites | None) -> str:
    if result.blocked_by_antirequisite:
        return "antirequisite"
    if result.unresolved:
        return "unresolved"
    if result.satisfied:
        return "compliant"
    kinds = {
        record.requisite_items[label].kind
        for label in result.unmet
        if record and label in record.requisite_items
    }
    if "admission" in kinds:
        return "unmet_admission"
    if "credit_points" in kinds:
        return "unmet_credit_points"
    return "unmet_academic"


def _rule_depth(record: SubjectRequisites | None) -> int:
    if record is None or not record.requisite_rule:
        return 0
    return depth(parse(record.requisite_rule))


def generate_scenarios(
    students: list[Student],
    catalogue: dict[str, Subject],
    requisites: dict[str, SubjectRequisites],
) -> list[ComplianceScenario]:
    scenarios: list[ComplianceScenario] = []
    for student in students:
        for code in catalogue:
            record = requisites.get(code)
            result = evaluate_requisites(code, student, requisites)
            scenarios.append(
                ComplianceScenario(
                    scenario_id=f"{student.student_id}-{code}",
                    student_id=student.student_id,
                    subject_code=code,
                    failure_mode=classify_failure(result, record),
                    rule_depth=_rule_depth(record),
                    expected_satisfied=result.satisfied,
                    explanation=result.explanation,
                )
            )
    return scenarios


def summarise(scenarios: list[ComplianceScenario]) -> dict[str, int]:
    return dict(Counter(scenario.failure_mode for scenario in scenarios))


def write_jsonl(path, scenarios: list[ComplianceScenario]) -> None:
    body = "\n".join(
        json.dumps(scenario.model_dump(), ensure_ascii=False) for scenario in scenarios
    )
    Path(path).write_text(body + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scenarios.py -v && ruff check src tests`
Expected: PASS, ruff clean.

- [ ] **Step 5: Generate the suite over the real dataset**

```bash
python3 -c "
import json
from pathlib import Path
from enrolment.models import Student, Subject, SubjectRequisites
from enrolment.scenarios import generate_scenarios, summarise, write_jsonl

load = lambda name: json.loads(Path(f'data/{name}.json').read_text())
catalogue = {c: Subject.model_validate(v) for c, v in load('catalogue').items()}
requisites = {c: SubjectRequisites.model_validate(v) for c, v in load('requisites').items()}
students = [Student.model_validate(v) for v in load('students').values()]

scenarios = generate_scenarios(students, catalogue, requisites)
write_jsonl('data/compliance_scenarios.jsonl', scenarios)
print(len(scenarios), 'scenarios')
print(summarise(scenarios))
"
```
Expected: a few thousand scenarios and a breakdown across failure modes. Check the `unresolved` count — those cases must be excluded from the violation-rate metric rather than scored, so a large count is a signal to improve `classify_item` in `evop.py`.

- [ ] **Step 6: Commit**

```bash
git add src/enrolment/scenarios.py tests/test_scenarios.py data/compliance_scenarios.jsonl
git commit -m "feat(enrolment): generate compliance scenarios from the real requisite graph"
```

---

## Done

Full verification:

```bash
pytest -q && ruff check src tests
```

Both must be clean. At this point the design spec's scope is complete and the
next spec — the MCP proxy and policy engine — can begin, consuming
`evaluate_requisites` and `exceeds_credit_cap` through a policy pack.
