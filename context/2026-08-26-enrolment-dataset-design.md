# Design: Enrolment domain dataset and requisite rule engine

**Status:** draft for review
**Date:** 2026-08-26
**Project:** 32933 Research Project (Type I) — MCP Policy Guard
**Relates to:** `RevisedPlan.docx` (supervisor's enrolment-domain proposal), `context/threat-model.md`

## Scope

This spec covers **one layer only**: the enrolment case-study data and the
deterministic requisite rule engine that operates on it.

In scope:

- Acquiring real UTS catalogue and requisite data for course C04296
- The on-disk data model for subjects, requisite rules, course structure
- Synthetic student records generated against the real catalogue
- A requisite rule parser and evaluator
- Programmatic generation of business-rule compliance scenarios

Out of scope, each needing its own spec later:

- The MCP proxy and policy engine (`src/policy_guard/`)
- The enrolment MCP server exposing tools to the agent
- The adversarial attack suite
- The evaluation harness and metrics
- The demo frontend

The layering matters for the project's central claim. `src/policy_guard/`
must contain no enrolment-specific symbol. Everything described here lives
behind a data adapter and ships as a loadable policy pack. The
domain-agnostic claim should be provable from the file layout, not argued.

## Why this exists

The revised plan assumed the supervisor would hand over a subject and
prerequisite database. That database is not available yet, and waiting for
it blocks weeks 4–7. This spec removes the dependency by building the
dataset from public UTS sources.

Two findings from reconnaissance on 2026-08-26 shape the design.

**The course handbook does not carry requisite data.** Subject pages at
`coursehandbook.uts.edu.au/subject/2026/<code>` return `pre_requisites: ""`,
`requisites: []` and `enrolment_rule: []` for every subject checked
(31251, 41039, 32004, 48023, 42890, 32146). UTS moved requisites out of the
handbook and links out instead.

**The requisite data lives in the eVOP access-conditions system**, at
`studentforms.uts.edu.au/evop/access/search.cfm?subjectcode=<code>`, is
served without authentication, and is richer than the plan assumed: it
publishes boolean rule expressions rather than flat prerequisite lists.

## Data sources

| Source | URL pattern | Provides |
|---|---|---|
| Course handbook | `coursehandbook.uts.edu.au/course/2026/c04296` | course structure, credit point targets |
| Handbook AoS | `coursehandbook.uts.edu.au/aos/2026/<CODE>` | majors, sub-majors, elective streams |
| Handbook subject | `coursehandbook.uts.edu.au/subject/2026/<code>` | name, credit points, description, offerings |
| eVOP access conditions | `studentforms.uts.edu.au/evop/access/search.cfm?subjectcode=<code>` | requisite and anti-requisite rules |

The handbook is a Next.js application. All page data is available as JSON in
the `__NEXT_DATA__` script tag under
`props.pageProps.pageContent`, so extraction is a JSON traversal, not HTML
scraping. `curriculumStructure.container[].relationship[]` carries
`academic_item_code`, `academic_item_credit_points`, `academic_item_url` and
a `parent_connector` of AND or OR. Majors and streams resolve through
`academic_item_url`, which uses the `/aos/<year>/<CODE>` route.

The eVOP pages are server-rendered HTML and do need parsing, but the
structure is stable: a rule expression line followed by a numbered item
table.

### Access and provenance

`coursehandbook.uts.edu.au/robots.txt` is `User-agent: * / Disallow: /`.
`studentforms.uts.edu.au` serves no robots.txt.

Constraints adopted in response:

- Fetch once, cache to disk, commit the cache. Experiments never hit UTS.
- Bounded to course C04296: roughly 100–150 distinct subjects.
- Serial requests with a delay between them. No concurrency.
- Every record stores its `source_url` and `fetched_at` for provenance.
- Raw responses retained under `data/raw/` so parsing can be re-run without
  re-fetching.

**Action required:** confirm with the supervisor before the full fetch.
He has offered a database extract; if he provides it, the fetcher becomes a
fallback and this question disappears. Either way the decision should be
recorded rather than assumed. The thin slice described below is small enough
to proceed with in the meantime.

## The requisite rule language

This is the core finding and the reason the pivot strengthens the project.

Measured over a sample of 30 subjects drawn from the C04296 Cybersecurity
major and the IT electives stream: 24 carry rule expressions, 6 have no
requisite record. Item type counts across the sample: 172 admission
requisites, 37 academic requisites.

Grammar:

```
rule    := expr
expr    := term { "OR" term }
term    := factor { "AND" factor }
factor  := "(" expr ")" | label
label   := digit+ letter*
```

Labels index into an item table published alongside the rule. Real examples,
in ascending order of difficulty:

```
32310   (1)
32009   ((1) OR (2 AND 3))
32509   ((1 OR 1a OR 1b OR 1c OR 1d OR 1e OR 1f) AND 2)
32146   ((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))
32513   (((1 OR 1a)) OR ((2 OR 2a) AND (3 OR 3a)) OR (4 AND (5 OR 5a)) OR (6))
32113   (((1 OR 1a OR 1b OR 1c) AND (2 OR 2a OR ... OR 2k)) OR ((3 OR 3a)))
```

Anti-requisites carry their own independent rule over their own item table,
e.g. 32570 has `(1 AND 2 AND 3)`.

Item payloads fall into distinguishable kinds:

- **subject** — `48024 Programming 2`
- **area of study** — `MAJ02902 36cp Information Technology Major MBA`
- **credit points** — `Must have completed at least 48 credit points in
  48.0000000000 Credit Points spk(s): C04379 - Master of ...`
- **admission** — `Admission into C04296 Master of Information Technology
  (Extension)`

Two properties make this the right testbed for the project's thesis. The
expressions are deep enough that evaluating them against a transcript is
genuinely error-prone for a language model, and they are institutionally
real rather than authored by the researcher. 32146 and 32113 are the
quotable examples for the report.

There is also a pleasing alignment with the tool surface: the eVOP page
tells students that if a requisite blocks them they should submit an
e-Request. The escalation path in the real system is exactly the
`submit_erequest` tool in the plan.

## Data model

Four JSON files under `data/`, plus raw cache under `data/raw/`.

`catalogue.json` — subjects by code:

```json
{
  "32146": {
    "code": "32146",
    "name": "Data Visualisation and Visual Analytics",
    "credit_points": 6,
    "description": "...",
    "sessions": ["Autumn", "Spring"],
    "source_url": "...",
    "fetched_at": "2026-08-26T..."
  }
}
```

`sessions` is derived from `offering[].teaching_period`, which carries values
such as "Autumn Session" and "Spring Session". The `start_date` and `end_date`
fields on the same records are published empty, which is why census dates must
be configured synthetically (see open question 4).

Credit points vary meaningfully across the electives stream (1, 2, 3 and 6
credit point subjects all appear), which gives the credit-cap policy real
work to do rather than trivial arithmetic.

`requisites.json` — rules by code:

```json
{
  "32146": {
    "requisite_rule": "((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))",
    "requisite_items": {
      "1":  {"kind": "subject", "type": "academic", "subject": "31250", "text": "31250 Introduction to Data Analytics"},
      "2":  {"kind": "subject", "type": "academic", "subject": "48024", "text": "48024 Programming 2"}
    },
    "antirequisite_rule": "(1 AND 2 AND 3)",
    "antirequisite_items": { "1": {"kind": "subject", "subject": "320146", "text": "..."} },
    "source_url": "...",
    "fetched_at": "..."
  }
}
```

Every item retains its raw `text` regardless of how well it parsed. An item
whose payload does not match a known kind is stored as
`{"kind": "unparsed", "text": "..."}` and surfaced in a coverage report
rather than dropped. Silent loss of a requisite would corrupt every
downstream metric.

`course.json` — the C04296 structure, preserving container nesting, credit
point targets and `parent_connector`, so that block-level rules ("18cp of
core", "select one major") remain enforceable and are not flattened away.

`students.json` — synthetic records:

```json
{
  "S1000001": {
    "student_id": "S1000001",
    "course": "C04296",
    "admitted_to": ["C04296"],
    "major": "MAJ02093",
    "completed": [{"code": "32541", "session": "2025-AUT", "credit_points": 6}],
    "enrolled": ["32144"],
    "credit_point_cap_per_session": 24
  }
}
```

All student data is synthetic. Only catalogue and requisite data is real, so
no ethics approval is required, consistent with the revised plan.

## Rule evaluator

A pure module, no I/O, no network, no model:

```python
def evaluate_requisites(subject_code, student, catalogue, requisites) -> RequisiteResult
```

Returning:

```
RequisiteResult
  satisfied: bool
  rule: str                       # the original expression, for the audit log
  items: dict[label, ItemOutcome] # per-item satisfied + reason
  unmet: list[str]                # minimal set of labels that would flip the result
  explanation: str                # agent-readable prose
```

Two stages, deliberately separated:

1. **Parse** the expression into an AST over labels. Domain-independent,
   testable against a truth table with no catalogue involved.
2. **Resolve** each label against student state by item kind: `subject`
   checks `completed`; `credit_points` sums completed credit points against
   the threshold; `aos` checks the student's major or sub-major; `admission`
   checks `admitted_to`.

Anti-requisites evaluate through the same machinery over the anti-requisite
table; if the anti-requisite rule evaluates true, enrolment is blocked.

The `explanation` field is not cosmetic. It becomes the body of the proxy's
deny message, which is how a policy block turns into a helpful sentence in
the chat rather than an opaque error. Deny messages are written to be
agent-readable by design.

### Open modelling questions

These need a decision before implementation and should be raised with the
supervisor, who knows the institutional semantics:

1. **Anti-requisite conjunction.** 31251 publishes
   `Anti-requisite rule: (1 AND 2)` over two subjects. Read literally, a
   student is blocked only if they completed both. Whether that is the
   intended institutional reading is not something the page states. The
   evaluator will implement the literal reading and flag the assumption.
2. **Admission requisites** are the dominant item type in the sample (172 of
   209). Modelling them requires the student record to carry course and
   area-of-study admission, which the model above does. Confirm this is the
   right granularity.
3. **Credit-point requisites** embed a course scope
   (`spk(s): C04379 - ...`). Whether the threshold applies to credit points
   completed within that course or overall needs confirmation.
4. **Census dates and session offerings** appear in neither source. These
   will be configured synthetically and clearly labelled as such.

## Compliance scenario generation

The revised plan proposed 10–15 hand-written benign non-compliance
scenarios. With a real rule graph, they can be generated instead: sample
(student history, target subject) pairs, evaluate the rule exactly, and keep
cases that fail, stratified by failure mode — unmet academic requisite,
triggered anti-requisite, credit cap exceeded, admission requisite unmet.

This is a material improvement to the evaluation, for three reasons. It
yields hundreds of cases rather than fifteen, giving the violation-rate
metric statistical power. It removes the validity threat that the same
person authored both the policies and the tests. And the generator is itself
a reportable artifact.

Stratification by rule depth also gives a difficulty gradient for free: a
depth-1 rule like 32310's `(1)` against a depth-4 rule like 32113's. The
expectation that model accuracy degrades with rule depth while the evaluator
stays exact is precisely the headline result.

## Testing

- **Parser unit tests** over the six real expressions quoted above, plus
  malformed input.
- **Property test:** for random assignments of truth values to labels, the
  parsed AST agrees with a brute-force truth-table evaluation. Establishes
  parser correctness independently of any domain reasoning.
- **Golden fixtures:** committed eVOP and handbook responses for a handful
  of subjects, so parsing is tested without network access.
- **Coverage report:** count of `unparsed` items per fetch, asserted at zero
  for the thin slice before widening.
- **Round-trip test:** every subject referenced by a requisite item exists in
  the catalogue, or is explicitly recorded as out-of-scope.

## Build sequence

**Thin slice first** — core (32541, 43030, 32144) plus the Cybersecurity
major, roughly 30 subjects. Proves fetch, parse and evaluate end to end and
is cheap to discard if the grammar has edge cases the sample missed.

1. Fetcher with on-disk cache and provenance
2. Handbook extractor (`__NEXT_DATA__` traversal) → `catalogue.json`, `course.json`
3. eVOP extractor → `requisites.json`
4. Rule parser and evaluator, with tests
5. Coverage report; widen to the full C04296 tree only once it reports clean
6. Synthetic student generator
7. Compliance scenario generator

Steps 1–4 are the thin slice.

## Risks

| Risk | Mitigation |
|---|---|
| robots.txt disallow on the handbook | Bounded one-time fetch, cached and committed; supervisor sign-off before widening |
| eVOP HTML structure changes | Raw responses committed; parsing re-runnable offline |
| Grammar edge cases beyond the 30-subject sample | Coverage report gates the widening step; `unparsed` items are never dropped |
| Requisite semantics misread | Open questions above resolved with supervisor, assumptions stated in the report |
| Domain work crowding out the proxy | This layer is a testbed; the thin slice is the timebox |

## Follow-on specs

1. Proxy and policy engine (completing the vertical slice; `proxy.py` is
   currently a stub)
2. Enrolment MCP server and policy pack
3. Adversarial attack suite
4. Evaluation harness and metrics
5. Demo frontend
