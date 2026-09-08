# Brief: the demonstration chatbot (Protection ON / OFF)

**32933 Research Project (Type I) · UTS · Spring 2026**
**Specification:** `context/project-spec.md`
**Related:** `context/2026-08-26-enforcement-layer-design.md` (the proxy),
`context/2026-08-31-scenario-suite-design.md` (the scenarios),
`context/2026-09-02-agent-loop-design.md` (subsystem A),
`context/2026-09-02-adversarial-runs-design.md` (subsystem B),
`context/2026-09-03-metrics-design.md` (subsystem D)
**Date:** 2026-09-07
**Origin:** supervisor clarification of the intended final demonstration.

---

## 1. The requirement, as given

The final demonstration is to be a **chatbot interface that makes the effect of
the enforcement layer visible**. Its central feature is a **Protection ON / OFF
toggle**: the same attack scenario, run twice, differing only in whether the
policy layer is active.

The chatbot talks to the existing student-handbook data through the existing
MCP tools and the existing business rules. It does not introduce a second
domain, a second rule engine, or a second set of scenarios.

**Protection OFF** — user or injected input → chatbot → LLM → MCP tool request →
tool executes → potentially unsafe or rule-violating result.

**Protection ON** — the same, with the policy layer interposed between the tool
request and the tool: inspect the call and its arguments, allow or deny by
policy, and only then forward.

**The toggle controls the defence, never the attack.** The injected payload,
the task, the persona and the tool set are identical on both arms. A demo in
which the OFF arm is fed a different, nastier input is not a comparison; it is
theatre, and nothing observed in it can go in the report.

The UI should show, at minimum: the conversation, the toggle, the MCP tool
being requested, its arguments, whether the call was allowed or blocked, the
policy's reason when it was blocked, and the final chatbot reply. A small
policy/debug panel is welcome. This is a research prototype, so simple.

---

## 2. Ground truth: what already exists

Anyone acting on this brief must read the repository before designing, because
several things the requirement asks for are already built and several things it
assumes are not.

**The toggle already exists.** `python -m policy_guard --enforcement on|off`
(`src/policy_guard/__main__.py:117`). Critically, the OFF arm is *not* a
bypass of the proxy: both arms compute the verdict and write it to
`decisions.jsonl`; only the forwarding decision is forced. Measured overhead
was p50 0.067 ms on and 0.063 ms off, so the two arms are not a cost
comparison — and the demo must not present them as one.

**The scenarios already exist.** Sixteen YAML files under `attacks/scenarios/`:
eight injected across four attack classes, eight benign twins. Each carries a
task, an injected `payload` with its `location`, a scripted `replay` of tool
calls with their expected verdicts, and a `final_state` block.

**The run machinery already exists.** `src/enrolment/agent/adversarial.py`
`run_one()` writes the task and overlay, launches the proxy with the arm's
`--enforcement` flag, and drives `src/enrolment/agent/loop.py` against a local
Ollama model. A run directory holds `task.yaml`, `overlay.yaml`,
`decisions.jsonl`, `transcript.jsonl`, `state.json`, `manifest.json`. Every
field the UI wants to display is already written to disk by this code.

**There is no chatbot and no frontend of any kind.** No web server, no HTTP
layer, no browser code. The agent loop is headless and batch-oriented.

**Only two of the four policies exist.** `allowlist` and `arg_validation` are
real. `taint.py` and `limits.py` are one-line stubs.

---

## 3. Constraints that must not be broken

These come from findings already recorded in this repository. Contradicting one
of them does not make the demo better; it makes it unciteable.

### 3.1 The OFF arm is not guaranteed to misbehave

The first adversarial campaign (`experiments/runs/adversarial/first`, 270 of 280
runs scored) returned **0% attack success in every measurable class on both
arms** — because `submit_erequest`, the call both measurable classes need in
order to land, was proposed **zero times across all 280 runs**. The local models
(`llama3.2`, `llama3.1:8b`) never mounted the attacks. Their effective tool-call
depth is about one per turn.

A live demo driving one of those models will therefore, much of the time, show
Protection OFF behaving perfectly well. The comparison collapses.

**The design must resolve this explicitly.** The options are a more capable
model, using each scenario's deterministic `replay` script instead of a live
model, or a live model with a scripted fallback. Whichever is chosen, the UI
must **label which one produced the run on screen** — a scripted replay
presented as a model's own decision would misrepresent the finding.

### 3.2 Only four scenarios can do the comparison at all

`deliverable: true` marks the scenarios with an implemented payload-delivery
mechanism, and therefore a meaningful enforcement-off arm:

| Scenario | Class | OFF arm |
|---|---|---|
| SC-02, SC-04, SC-06 | output-injection | yes |
| SC-07 | exfiltration | yes |
| SC-01, SC-05 | tool-shadowing | **no** — no delivery mechanism |
| SC-03, SC-08 | poisoned-description | **no** — no delivery mechanism |

Those four are the demo's material. The other four may be shown as ON-arm-only
illustrations if they are labelled as such, and must never be shown as an ON/OFF
comparison.

### 3.3 Two of the requirement's example attacks have no implementation

The requirement lists "malicious content from an untrusted source influencing a
tool call" (taint tracking) and "malformed or suspicious MCP arguments" and rate
or scope abuse (limits). Both policies are stubs. The design must either drop
these from the demo or state plainly that implementing them is part of the work,
with the cost.

Note that the trust labels they would consume already exist: `configs/enrolment.yaml`
marks `search_subjects` and `get_subject_details` as `untrusted` and everything
else `trusted`. The data for taint tracking is there; the policy is not.

### 3.4 No inventing scenarios

Scenarios must come from, or be consistent with, the real dataset and the real
rules — the C04296 catalogue in `data/`, the requisite evaluator in
`src/enrolment/rules.py`, and the nine tools in `src/enrolment/server.py`
(`search_subjects`, `get_subject_details`, `get_student_record`,
`check_prerequisites`, `enrol`, `drop`, `submit_erequest`, `get_study_plan`,
`preview_major_change`, `get_session_options`). Students are synthetic; the
catalogue and the requisite expressions are institutionally real, and that is
what makes the demo worth watching.

### 3.5 Do not rewrite the architecture

The proxy, the policy engine, the enrolment server and the agent loop stay as
they are. The chatbot is a presentation layer over machinery that already runs.
If the demo needs something the engine cannot do, that is a finding to report,
not a licence to restructure.

---

## 4. What the analysis must deliver, before any code

Inspect the repository first, then answer:

1. **Your understanding of the requirement** — in your own words, including
   anything in §1 you think is mistaken.
2. **How it maps to the current codebase** — where the chatbot, the MCP
   integration, the tool calls, the handbook rules and the existing enforcement
   components actually live.
3. **What Protection OFF technically bypasses** — precisely which check stops
   applying, and what still runs. Name the code path.
4. **What Protection ON enables** — which policies fire, in what order, on which
   arguments.
5. **The end-to-end flow for both modes** — including where the run artifacts
   the UI reads are produced.
6. **Three to five concrete scenarios**, drawn from §3.2's deliverable set,
   each stating the injected payload, the tool call it induces, the OFF-arm
   outcome, the ON-arm verdict, and the policy that produces it.
7. **Files and components to create or modify** — separated into new files and
   edits to existing ones.
8. **A phased implementation plan** — with the first phase small enough to prove
   the end-to-end path before any UI polish.

Two further questions the requirement does not raise but the report needs:

- **Is the demo a demonstration only, or does it produce citable numbers?** If
  the latter, its runs must be scoreable by `enrolment.metrics.report` like any
  other campaign, which constrains the run-directory layout.
- **What does the demo do when the model simply does not attempt the attack?**
  §3.1 says this is the common case. Silence is a legitimate outcome and the UI
  has to say so rather than showing an empty panel.

**Do not implement anything until this analysis is complete and approved.**

---

## 5. Decisions taken

**2026-09-07 — what drives the model (resolves §3.1).**
The demo is built on each scenario's deterministic `replay` block first; a
live model is added later, as a second mode, not as the foundation.

The reason is §3.1: across 280 runs of the first campaign the local models
proposed `submit_erequest` zero times. A demonstration whose OFF arm depends
on a model choosing to misbehave is a demonstration that can fail in front of
the audience it exists for. The scripted path always lands, so the ON/OFF
difference is always visible.

Two conditions on this:

- **The UI must label a scripted run as scripted**, on screen, in both arms.
  A replayed call is what the scenario says an attack would induce, not what
  a model decided. Presenting it as the latter would misstate the finding —
  the same distinction `context/2026-08-31-scenario-suite-design.md` already
  draws about the replay suite.
- **Live-model mode is still built** (phase 3), because the model-in-the-loop
  arm is what the report's claim rests on. Scripted replay demonstrates that
  the layer blocks the call; only a live model can show that a model would
  otherwise have made it.

**Still open:** whether the demo's runs must be scoreable by
`enrolment.metrics.report` — that is, whether the demonstration produces
citable numbers or is illustration only. Cheap to decide now, expensive to
retrofit, because it constrains the run-directory layout.

---

## 6. Phase 0: the probe, and what it found

**Run 2026-09-07. SC-06, both arms, no model.** A throwaway script replayed
SC-06's two scripted calls through the real proxy on each arm and diffed
`state.json`:

```
enforcement ON   S9000004 enrolled: ['32541', '43030', '32548', '32547']
enforcement OFF  S9000004 enrolled: [         '43030', '32548', '32547']
```

Subject 32541 disappears with enforcement off. **The demo has a story.** The
deny reason is already fit to show a non-technical audience:

> `'drop' is not on the allowlist for task 'SC-06' (allowed: check_prerequisites,
> get_session_options, get_student_record, get_study_plan, get_subject_details…)`

`get_study_plan` executed on *both* arms. Worth showing deliberately: it is
what stops the demo reading as "the proxy blocks everything".

### 6.1 The replay runner cannot supply the OFF arm, by design

`scenario_runner.run_scenario` raises on `enforcement="off"`, and its message
gives the reason: replaying hand-scripted calls unenforced would mutate state
and "be indistinguishable from a successful attack when none was mounted".

That guard is correct and stays. The consequence for this work is that **the
demo backend drives the proxy directly** rather than reusing
`run_scenario` — issuing the scenario's `replay` calls itself against a proxy
launched with the arm's `--enforcement` flag. Phase 0's probe is the shape.

The guard's reasoning also transfers: a scripted OFF arm mutates real state
and looks exactly like a successful attack. On screen it must be labelled as
scripted, every time, on both arms.

### 6.2 There are two policy packs, and the wrong one fails toward a false pass

`configs/enrolment.yaml` points `students_path` at `data/students.json`.
The scenario personas (S9000001–S9000004) are not in that file; they live in
`data/personas.json`, which is what `configs/enrolment-personas.yaml` points
at. `scenario_runner._students_baseline_path` already hardcodes the personas
pack for this reason.

Phase 0's first run used the general pack. Every call failed with
`no such student: S9000004` — including the `drop`, so the OFF arm mutated
nothing and the two arms matched. **A configuration error presented as a
clean enforcement result.** The demo must fail loudly if its pack has no
record for the scenario's persona, rather than rendering an empty diff.

---

## 7. Implementation phases

0. ~~**Probe.**~~ ✅ 2026-09-07, §6.
1. **Headless demo API.** One call runs one scenario on one arm and returns
   the transcript, the decisions and the state as JSON. No streaming, no
   browser. Proves the path end to end.
2. **The page.** Chat transcript, enforcement toggle, decision table (tool,
   arguments, verdict, reason). Ugly is acceptable.
3. **Streaming**, and live-model mode beside scripted replay.
4. **Polish.** Side-by-side arms, scenario picker, the `state.json` diff,
   reset button.
