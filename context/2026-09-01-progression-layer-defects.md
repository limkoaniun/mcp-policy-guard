# Design record: what the progression layer's build got wrong

**32933 Research Project (Type I) · UTS · Spring 2026**
**Implements:** `context/2026-08-31-scenario-suite-design.md`
**Date:** 2026-09-01

---

`context/project-plan.md` closes with a note that the data layer's instructive
failure mode was not the model being wrong but "the scaffolding around it being
confidently wrong without saying so", and that the pattern is worth a paragraph
in the final report. Building the progression layer produced four more
instances, all in code written *for* this project rather than by the model under
test. They are recorded here because a report arguing that deterministic
enforcement beats a probabilistic model should be honest about how easily the
deterministic part goes wrong.

Each was caught, and each is fixed and regression-tested on this branch. What
matters for the report is not that they happened but *what kind* of thing each
was, and which check caught it.

## 1. A tie-break the specification never defined

The first assignment solver added an index-derived term to its cost function —
`(i + j) % m` over the positions of a subject and a block in their lists — to
distribute dual-listed subjects across blocks.

It was invented to satisfy a test that asserted the two subjects land in
*different* blocks. The specification says nothing of the sort: both blocks had
room, so either arrangement counts the same credit. The test asserted a
distribution the rules do not determine, and the algorithm was bent to fit it.

The consequence, had it shipped: a student's reported placement would depend on
the order blocks happened to be enumerated in. For a project whose contribution
is explainable, reproducible enforcement, that is disqualifying — the answer
could not be justified from the handbook.

**Caught by:** reading the implementer's own summary of what it had done. The
test suite was green.

## 2. Modelling indivisible credit as divisible flow

Block satisfaction was specified as a flow problem and implemented as min-cost
max-flow, with each subject's credit points as an edge capacity. Flow divides.
Credit does not: a 12cp subject counts wholly against one block or not at all.

Against the committed dataset this over-reported. Student `S1000038` in
`data/students.json` was told they held 12 credit points where the correct
figure is 11. Across 400 randomly generated small instances, the flow solver
beat the true whole-subject optimum in 40.

The replacement is an exact whole-subject search, verified against exhaustive
enumeration on 800 instances: zero optimality mismatches, zero splits, zero
partial-credit placements.

**Caught by:** cross-checking the solver against brute-force enumeration. Both
the task review and the re-review had passed it as a correct min-cost max-flow
— which it was. It was a correct solution to the wrong problem.

## 3. A cap that only bound on the first call

The enrolment validators loaded `students.json` once at proxy start and closed
over it, while the upstream server mutated run state in
`<run-dir>/state.json`. The validator's view of the student's session load was
frozen at the baseline.

A student with an 18cp load against a 24cp cap was permitted five consecutive
enrolments, reaching **48cp — twice the cap, every call allowed**. The cap did
not leak by one call; after the first it never bound again.

This is the most serious of the four, because it is a failure of the exact
property the project claims. `context/project-spec.md` §4 argues the proxy earns
its place by enforcing "at the *composition* of tool calls rather than
per-endpoint". A cap that binds only on the first call is per-endpoint
validation wearing a proxy's clothes, and an enforcement-on run would have
scored the defence higher than it deserved.

**Caught by:** a reviewer reading the closure against the run-state lifecycle.
No test covered repeated calls, which is why the branch now has one.

## 4. Enforcement that denied the tools the attack suite needs

Principal binding denied any call omitting the principal argument — reasonably,
since an action that cannot be attributed cannot be verified. But
`search_subjects` and `get_subject_details` take no `student_id`, and a
load-time guard had just made binding mandatory for this pack. The shipped
configuration could not read the catalogue at all.

Those two tools carry every piece of attacker-influenceable text in the domain.
The adversarial suite would have had no injection surface, and its results would
have looked like a total defensive success.

The fix has the pack declare which tools are *exempt* rather than which are
*scoped*, so that forgetting to list a tool leaves it enforced rather than
silently unbound.

**Caught by:** the whole-branch review driving the shipped configuration end to
end. Every task-level test was green: one e2e test allowlisted
`get_subject_details` and never called it.

## What the four have in common

None was a crash, and none failed a test. Every one of them produced a
confident, plausible, wrong answer — and in three of the four cases the error
ran in the direction that flatters the system: more credit counted, a cap that
appeared to hold, an attack surface that appeared defended.

That directionality is the finding worth reporting. A defence evaluated by
scaffolding that fails toward "defended" will report a better number than it
earned, and nothing in a green test suite says so. The mitigations that actually
worked here were not more tests of the same kind: they were cross-checking an
optimiser against exhaustive enumeration, reading a closure against the
lifecycle of the state it captures, and exercising the shipped configuration end
to end rather than a fixture that resembles it.

## Two things left undone

- `src/policy_guard/__main__.py` hardcodes the flag name `--students` when
  deriving the upstream's records path from the pack. The value comes from the
  pack; the name does not. It is a small scratch on the claim that the generic
  layer never learns the domain, and the first thing to fix if a second domain
  is added.
- The unresolved-requisite deny path is unreachable against this dataset, which
  has zero unparsed items by construction. It is exercised only by unit tests
  with synthetic fixtures, so the claim "the layer never permits what it could
  not verify" is verified in the small, not end to end.
