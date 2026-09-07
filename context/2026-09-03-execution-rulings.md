# Rulings taken during autonomous execution

**32933 Research Project (Type I) · UTS · Spring 2026**
**Covers:** subsystem B (`context/2026-09-02-adversarial-runs-plan.md`) and
subsystem C (`context/2026-09-03-compliance-runs-plan.md`)
**Date:** 2026-09-03

---

## What this document is, and why it exists

Both subsystems were built by dispatching one implementer per task and one
reviewer after each, with a whole-branch review at the end. That process runs
without stopping to ask, which means decisions get taken on the researcher's
behalf: conflicts between the plan and the code, findings that contradict what
the plan mandates, and scope calls about what a fix wave should cover.

Those decisions are recorded in a ledger that lives in a git-ignored scratch
directory and is deleted when the branch is finished. This file is what
survives. Each entry says what was decided, why, and **what it costs if the
decision was wrong**, so the decisions can be reviewed and reversed rather than
quietly inherited.

Two entries below record decisions that were themselves wrong and had to be
reversed. They are kept rather than tidied away: a project arguing that
deterministic checking beats confident guessing should not hide the places
where its own controller guessed confidently and was corrected.

---

## Subsystem B — adversarial runs

### B1. Work on a branch, not a git worktree

`.venv/` is git-ignored and every verification command in the plan invokes
`.venv/bin/python`. A fresh worktree would have needed a full editable
reinstall before any task could verify. The project already works this way
(`feat/agent-loop` → PR #4).

*Cost if wrong:* the working tree is not isolated from other work in this
checkout.

### B2. Task 2 must also amend a test asserting the word "harness"

The plan reworded `run_scenario`'s broad enforcement-off refusal, dropping a
word `test_the_enforcement_off_arm_is_refused_outright` matched on. The test's
real intent is that the broad refusal is distinguishable from the narrow,
deliverability-based one; its docstring's claim that "there is no agent harness
yet" stops being true the moment subsystem B lands. Changed the match to
`"replay arm"` and rewrote the docstring.

*Cost if wrong:* a stale assertion masks which of the two refusals fired.

### B3. Leave `_ATTEMPT_SUFFIXES` unguarded at three entries

`RunSpec.replacement()` has no internal bound; `run_campaign`'s
`max_replacements=2` is the actual guard, and it is not exposed on the CLI, so
`attempt` cannot exceed 2. An out-of-range attempt would raise `IndexError`,
which is loud, rather than producing a silently wrong run id.

*Cost if wrong:* a future caller passing `max_replacements=3` crashes instead
of getting a clear message.

### B4. Replace `str.format` with `str.replace` for the system prompt

`task_for` used `system_prompt.format(principal=...)`, which raises on any
stray brace in a custom prompt — and the whole reason that parameter exists is
that subsystem D passes a different prompt whose prose may contain braces.
`{principal}` is the only placeholder anywhere in the codebase, so `.replace`
is behaviourally identical for every current use and cannot crash.

*Cost if wrong:* a prompt wanting several named fields would need a different
mechanism. No such requirement exists.

### B5. A test-count mismatch was a plan arithmetic error, not a defect

The plan's Task 4 said twelve new tests; the test file it specified verbatim
contained thirteen. The implementer kept the file and reported the mismatch
rather than deleting a test to hit the number, which is correct.

*Cost if wrong:* nothing — a count in a plan, not a contract.

### B6. Uphold a report-accuracy finding, but spend no review seat on it

A reviewer raised an Important finding against an implementer's *report*
rather than its code. The correction produced zero lines of code diff, in a
file about to be deleted, so the substantive response was to verify the
report's code claims directly instead of dispatching a review of an empty
change.

*Cost if wrong:* one fix round went unreviewed — bounded by touching no code.

### B7. **REVERSED.** Ruff C408 *is* enabled; the original ruling was wrong

An implementer justified a `dict(...)` → literal edit by citing ruff C408. It
was ruled a false claim, the implementer was made to retract it, and the
conclusion was handed to a reviewer as verified context — which produced a
finding for "fabricated technical justification".

**The check was invalid.** It ran `ruff check --select C408` against a file
containing no `dict()` call at all, so "All checks passed" demonstrated
nothing. Re-verified with a probe actually containing the construct: a plain
`ruff check` reports `C408 Unnecessary dict() call`. The implementer was right,
the finding was void, and both were withdrawn.

*Lesson recorded in the ledger at the time:* a negative lint result is only
evidence if the probe file contains the construct under test.

### B8. Fix `--limit`'s wording, not its behaviour — later amended

`--limit`'s help called it "chunks", implying pagination, while the
implementation is a rising cap: repeating the same `--limit N` makes no
progress, silently. Real pagination would need `run_campaign`'s resume rule
duplicated into `main` — and it is a subtle rule, since a `provider_error` run
on disk must *not* be skipped, being the trigger for its own replacement. One
rule in two homes is the defect species this codebase has closed twice.

**Amended at the final review:** `--limit` also *deleted* index rows for
already-completed runs past N, because `write_index` rewrites the file whole
from only the current invocation's spec list. The wording ruling stands; the
row-deletion half was a genuine data defect and was fixed by merging prior
index rows rather than by changing `--limit`'s semantics.

*Cost if wrong:* an operator repeating an identical `--limit N` sees no
progress; mitigated by help text that now says plainly it is a cap.

### B9. Fix the model-name bug in subsystem A's `ollama.py`, not in `DEFAULT_MODELS`

`preflight` rejected `llama3.2` because Ollama reports a bare-name pull as
`llama3.2:latest`, so the default campaign aborted telling the researcher to
pull a model they already had. Renaming the default models would have renamed
every run directory for a reason no later reader could see, and the design's §6
names those identifiers. A one-line matcher change in `model_fingerprint` is
the smallest correct fix, and that module is the one that was actually wrong.

*Cost if wrong:* this branch touched a module it did not otherwise own.

### B10. One fix wave, covering three Criticals, four Importants and two Minors

The final whole-branch review found defects the per-task reviews structurally
could not see. Two were the project's own cardinal sin — a fabricated
`duration_s: 0.0` for runs adopted on resume, and a preflight message asserting
something false. Both were fixed in a single dispatch rather than one fixer per
finding.

---

## Subsystem C — compliance runs

### C1. A test-count mismatch was again a plan arithmetic error

The plan's Task 2 said fifteen tests; the file it specified contained sixteen.
Same species as B5, same resolution: verbatim content wins.

### C2. The plan's Task 2 test file was missing `import pytest`

The instruction to add it sat in prose *after* the code block. Carried into the
dispatch explicitly so it was not missed.

*Cost if wrong:* an immediate `NameError` the implementer would have fixed
anyway.

### C3. Task 5's fixture was verified against the real dataset before dispatch

The tests hardcode `S1000001` / `32933` and expect the enforcement-on arm to
deny. Checked: that scenario exists with `failure_mode=unmet_academic`,
`rule_depth=3`, `expected_satisfied=False`. The student's completed list is
`32040, 32601, 42097, 42144, 42824, 42827` — no `32144`, which `32933`
requires. The denial is correct ground truth, and the student holds `42144`, a
near-miss of the required `32144`, which makes the fixture a stronger test than
intended.

### C4. **The implementation was right and the design's recorded numbers were wrong**

Two of the sampler's tests failed against figures the design had recorded. The
implementer refused to adjust the assertions and reported the disagreement,
which is what the design's verification standard demands.

The design's §5 figures were derived from a throwaway probe that drew from
pools in **file order**, while §8 requires the sampler to sort pools by
`scenario_id` before drawing — so the probe and the specification contradicted
each other from the moment both were written, and the probe was stale.

Re-derived against the shipped sampler: total 248, core 200, top-up 48, and
every failure mode at 25 — all unchanged, so the design's argument is
unaffected. What moved: top-ups are `unmet_admission 3 / unmet_aos 24 /
unmet_credit_points 21` (recorded as 4/24/20), and final depths are
`40/40/65/63/40` (recorded as 40/40/68/60/40). Design §13 records the
correction rather than hiding it.

*Cost if wrong:* none identified — the corrected figures come from running the
shipped code against the committed dataset, and the totals carrying the
argument never changed.

### C5. Correct the design and plan directly; send test assertions back to the implementer

The design and plan are the controller's own documents and not artifacts under
review; the test assertions are code and go through the normal loop.

*Cost if wrong:* nothing; the docs are not reviewed artifacts.

### C6. Uphold the domain-import finding and replace substring matching with an AST walk

`test_campaign_still_imports_nothing_from_the_domain` matched forbidden
substrings, so `from enrolment.scenarios_spec import Scenario` slipped through —
confirmed by running the check. That test guards the single property subsystem
C is built on: `campaign.py`'s domain-neutrality is why C reuses it unchanged,
and a guarantee resting on this codebase's convention of relative imports
rather than on the test claiming to enforce it is exactly the shape this
project keeps closing.

The replacement encodes the actual rule: absolute imports must not start with
`enrolment`, and any relative import at level ≥ 2 is forbidden outright, since
anything above `agent/` *is* the enrolment domain while level-1 siblings like
`.transcript` are legitimate.

*Cost if wrong:* a stricter test could reject a future legitimate import from a
parent package — but there is no such legitimate import by design.

### C7. Widen `COMPLIANCE_TOOLS` to match subsystem B — withhold only `drop`

**The most consequential ruling in either subsystem.**

`task.yaml`'s `allowed_tools` does not restrict what the model *sees* — the
proxy advertises all ten upstream tools on both arms. It decides what the
allowlist policy **denies on the enforcement-on arm**. Verified against the
real scenario files: B's sixteen scenarios grant nine of ten between them,
withholding only `drop`, so its allowlist is near-inert and the on/off
difference is dominated by the policies actually under test.

C withheld four. So `get_session_options`, `preview_major_change` and
`submit_erequest` were denied on the on arm and forwarded on the off arm, for
reasons with nothing to do with requisite rules. Since subsystem A measured
both models at an effective depth of one call, a model opening with
`get_session_options` — a plausible pre-enrolment call — burns its budget on a
refusal that happens on only one arm and may never reach `enrol`. That shifts
the on-arm attempt rate for a reason unrelated to enforcement, which is the
inversion design §7 exists to prevent, and it would have quietly biased §7's
attempt rate, benign completion and enforcement-overhead numbers.

The design's §6 asked only that the model "have a way to be right" and never
considered that a *narrow* allowlist makes the allowlist itself the variable.

Ground-truth safety was verified before widening: only `RunState.enrol` and
`RunState.drop` mutate enrolment; `submit_erequest` only appends to
`erequests.jsonl`; `preview_major_change` and `get_session_options` call
`state.get()`, which returns a deep copy by design. `drop`, the one mutating
tool, stays withheld.

*Cost if wrong:* the model carries three tools it does not need, adding noise —
but noise that lands identically on both arms, which is the property that
matters.

### C8. Uphold the `load_catalogue` and `not_attempted` findings

`load_catalogue` was unasserted behind a deliberately silent fallback: a shape
drift in `catalogue.json` would have run all 4,960 runs on a degraded prompt
with nothing catching it. And `not_attempted` named two opposite things —
`CallOutcome.NOT_ATTEMPTED` means the harness never sent a proposed call, while
design §7's run-level sense means the model never proposed the enrolment at
all. A subsystem D implementer finding the enum first would compute the inverse
of the metric, producing the "a weaker model scores as more compliant"
inversion §7 was written to close.

### C9. Fold in six Minors, defer one

Deferred: B uses `--scenarios` for its suite path while C uses `--suite`.
Renaming a flag is churn for no correctness gain.

### C10. **Partly wrong.** Copying B's live-test form fixed one axis and worsened another

The instruction to adopt subsystem B's live-test assertion form fixed a fragile
strict-equality check. But C previously **skipped** when the model made no tool
calls, while B's form passes vacuously — with `reached == []` and
`decisions == []`, every assertion is trivially true. So a harness fault
suppressing all tool calls now presents as a silent pass in **both** suites'
live tests, where C alone previously announced it.

The reviewer caught this and correctly classed it inherited rather than
introduced, since the copy was explicitly instructed. It was out of scope for a
fix wave with no successor.

**The right fix for both suites, when someone next touches them:** assert that
at least one call reached the proxy, and distinguish "the model proposed
nothing" — visible in the transcript's recorded proposals — from "nothing
reached the proxy", skipping only on the former.

*Cost if wrong:* a live-test regression in either suite reads as a pass;
mitigated by both live tests still asserting transcript existence, `task_id`
round-trip, `run_status` round-trip and a real model digest before that point.

---

## What to carry into subsystem D

Three of the rulings above bear directly on D:

- **C7** — the enforcement arms now differ only in `drop` and in the policies
  under test. If D compares attempt rates across arms, that is why the
  comparison is clean.
- **C8** — use the run-level sense of "attempted": an `enrol` proposal
  appearing anywhere in `transcript.jsonl`, whatever its `CallOutcome`. Do not
  use `CallOutcome.NOT_ATTEMPTED`, which is the near-inverse. For a run where
  `enrol` was proposed but truncated by `max_calls`, the run's `truncated`
  status in `index.jsonl` is what lets D exclude it.
- **C4** — the sample's composition is what the sampler produces, not what any
  earlier document recorded. `sample.jsonl` in each campaign directory is the
  authority for which scenarios were drawn and which stratum each belongs to.
