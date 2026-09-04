# Project plan: build order, schedule and risks

**32933 Research Project (Type I) · UTS · Spring 2026**
**Specification:** `context/project-spec.md`
**Last updated:** 2026-08-26

---

## How this project gets built

The guiding rule is **thinnest vertical slice first**. Each stage produces
something that runs and is testable on its own, rather than a layer that only
becomes meaningful once the layer above it exists.

### Layering

```
agent  ⇄  proxy (policy engine)  ⇄  enrolment MCP server  ⇄  data adapter  ⇄  data
```

Four boundaries, each replaceable without touching its neighbours. Two of them
carry load:

- **`src/policy_guard/` never learns about enrolment.** The domain arrives as a
  loadable policy pack. This is what makes "domain-agnostic" a property of the
  file layout rather than a claim in the report.
- **Nothing imports the real data directly.** Everything goes through the data
  adapter, so the synthetic fallback and the real catalogue are interchangeable.
  This is what removed the critical-path dependency on the supervisor's database.

### Stage order, with rationale

1. **Data layer first.** ✅ *Complete.* It has no dependencies, it is pure and
   testable, and it is where the project's central claim actually lives — an exact
   rule evaluator is the thing an LLM is being compared against. Building it first
   also surfaced the two facts that reshaped the plan: the handbook has no
   requisite data, and the real rules are boolean expressions.
2. **Proxy and policy engine.** The vertical slice that proves the architecture:
   MCP on both sides, `list_tools` and `call_tool` forwarded, every call through
   `PolicyEngine.evaluate()`, DENY returned as an MCP error, every decision logged.
   Allowlist policy only — the other three stay stubs.
3. **Enrolment MCP server and policy pack.** The seven tools over the data layer,
   with the enrolment rules loaded as a pack rather than compiled in.
4. **Remaining policies:** argument validation (which consumes the rule evaluator
   directly), taint tracking, rate and scope limits.
5. **Agent harness.** Local model via Ollama, fixed temperature, N repeats, seeded.
6. **Attack suite**, instantiated in the enrolment domain, plus one or two retained
   generic scenarios.
7. **Evaluation harness and experiments.**
8. **Analysis, ablations, demonstration frontend, report.**

### Two decisions to take early, not late

**Emit structured JSONL decisions from day one.** `PolicyEngine.audit_log` is
currently in-memory. Both the evaluation harness and the frontend need decisions on
disk. It is a small change now and a rework later.

**Write deny reasons for the agent to read.** They become the body of the proxy's
refusal, and therefore the sentence the student sees. Retrofitting this means
rewriting every policy's messages.

---

## Schedule

The revised plan's 13-week structure, annotated with actual status.

### Weeks 1–3 — foundation

| Item | Status |
|---|---|
| Literature review | **Outstanding** — `context/literature-notes.md` is still an empty template |
| Threat model | Drafted; extended 2026-08-26 to cover the benign non-compliance axis |
| Attack taxonomy | Four classes fixed; enrolment instantiation drafted in `attacks/README.md` |
| Business rule set | Established from real data: requisites, anti-requisites, credit-point thresholds, admission requisites |
| Database handover format | **Resolved differently** — supervisor's database unavailable, so the dataset is built from public UTS sources |

### Weeks 4–7 — build

| Item | Status |
|---|---|
| Rule parser and evaluator | ✅ Complete |
| Catalogue and requisite extractors | ✅ Complete |
| Cached fetcher with provenance | ✅ Complete |
| Dataset build pipeline and coverage gate | ✅ Complete; **full C04296 tree built 2026-08-26**, 93 subjects, clean |
| Synthetic student generator | ✅ Complete |
| Compliance scenario generator | ✅ Complete |
| MCP proxy and policy engine | ✅ Complete — shipped 2026-08-27 |
| Enrolment MCP server | ✅ Complete — shipped 2026-08-27 |
| Agent harness (Ollama) | ✅ Complete. Subsystems A (agent loop), B (adversarial runs), C (compliance runs) and D's scoring half (`context/2026-09-03-metrics-design.md`) all shipped. The ablations and prompting baseline remain. |

### Weeks 8–10 — experiments

Adversarial scenarios and compliance scenarios (generated, hundreds); runs with
enforcement on and off; attack success rate, rule violation rate, task
completion, overhead.

**Adversarial scenarios — half done.** The definitions and the replay half
exist: sixteen scenarios (eight injected across the four attack classes, eight
benign twins), a validating schema and loader
(`src/enrolment/scenarios_spec.py`), and a runner
(`src/enrolment/scenario_runner.py`) that replays each scenario's scripted
calls through the real proxy and judges the result from run artifacts —
specified in `context/2026-08-31-scenario-suite-design.md`, 395 tests
passing. The model-in-the-loop half does not exist: there is no agent
harness, so the calls each scenario replays are hand-scripted stand-ins for
what an attack would induce, not a model's actual output under injection, and
every enforcement-off arm is consequently unrun. The suite as it stands
demonstrates that the proxy denies the right calls for the right reasons; it
does not yet demonstrate that an attack would otherwise have succeeded. Four
of the eight injected scenarios (`deliverable: false`, for the two classes
with no implemented payload-delivery mechanism) will have no enforcement-off
arm even once the harness lands.

### Weeks 11–13 — analysis and write-up

Ablations of individual policies; the prompting baseline comparison;
demonstration frontend; final report.

---

## Immediate next steps, in order

1. ~~Run the dataset build.~~ ✅ Done 2026-08-26, thin slice then full tree.
   The thin slice found a requisite type reconnaissance had missed
   (`Academic corequisite`); after handling it, the full 93-subject build
   completed with zero unparsed items. 4,650 compliance scenarios generated.
2. **Email the supervisor.** One message covers the five open questions in §10 of
   the spec, and the data-access confirmation. Any answer unblocks work.
3. **Start the literature review.** It is the one Weeks 1–3 deliverable still
   outstanding, and it is on the critical path for the report rather than the code.
4. ~~**Specify and build the proxy.**~~ ✅ Done. The proxy, the policy engine, the
   enrolment MCP server, the study-plan progression layer, the scenario suite and
   subsystem A of the agent harness have all shipped since this list was written.
5. ~~**Build subsystems B, C and D's scoring half.**~~ ✅ Done. B: 280
   adversarial runs. C: 4,960 compliance runs over a 248-scenario stratified
   sample. D: `python -m enrolment.metrics.report` turns either into the §7
   figures with every number traceable to its runs.
6. **Run the campaigns and read the numbers.** B is about 30 minutes, C about
   four hours, both resumable.
7. **Build the ablations and the prompting baseline** — B and C re-run with
   each policy disabled and under the §7 baseline prompt. Two of four policies
   exist, so two can be ablated, and the report must say why not four.

---

## Follow-on specifications

Each gets its own spec → plan → implementation cycle, in this order:

1. ~~**Proxy and policy engine**~~ — specified in
   `context/2026-08-26-enforcement-layer-design.md`, which grew to cover the
   enrolment MCP server and all four policy families, decomposed into four
   sequenced slices
2. **Enrolment MCP server and policy pack** — including the admission-descriptor
   normalisation decision and the session-load model the credit-point cap needs
3. ~~**Progression layer**~~ — specified in
   `context/2026-08-31-scenario-suite-design.md`. The Layer 2 study-plan
   evaluator (`src/enrolment/progression.py`: block expansion, optimal
   assignment, carry-over, completion gap) and the personas
   (`data/personas.json`) are built, but the enforcement layer wired up so
   far is Layer-1-only: requisite, anti-requisite and credit-point-cap
   validation, from `src/enrolment/requisites.py`, wired into
   `ArgumentValidationPolicy` as `enrolment.policy_pack:build_validators`
   (`src/enrolment/policy_pack.py` imports `requisites`, not `progression`),
   loaded by import path so `src/policy_guard/` never imports `enrolment`
4. **Adversarial attack suite**
5. **Evaluation harness and metrics**
6. **Demonstration frontend** — note the dependency: the chat panel needs the
   agent harness, so it cannot be built before that lands. The *viewer* half
   (decision trace, enforcement on/off side by side, state diff) reads only
   artifacts that already exist and could be built at any time; a static
   generator producing one self-contained HTML file is the shape that survives
   leaving the machine. Building it early would also stress-test the
   `decisions.jsonl` format before slices 2-4 depend on it.

---

## Risks

| Risk | Mitigation | Status |
|---|---|---|
| Supervisor's database unavailable | Data adapter boundary; built from public sources instead | **Realised and handled** |
| robots.txt disallow on the handbook | Bounded one-time fetch, cached and committed; supervisor sign-off before widening | Open — awaiting confirmation |
| eVOP HTML structure changes | Raw responses committed; parsing re-runnable offline | Mitigated |
| Grammar edge cases beyond the 30-subject sample | Coverage report gates the widening step; unparsed items are never dropped | Mitigated |
| Requisite semantics misread | Open questions raised with supervisor; assumptions stated in the report | Open |
| Domain work crowding out the proxy | The data layer is done and timeboxed; the proxy is the next thing built | Contained |
| Frontend eating implementation time | Scheduled Weeks 11–13; may only read existing artifacts | Not yet live |
| Literature review slipping | Currently the only outstanding Weeks 1–3 item | **Live risk** |

---

## A note on this plan's predecessor

`context/2026-08-26-enrolment-dataset-plan.md` is the implementation plan for the
data layer, kept as a historical record. **Its reference code contains defects that
the merged implementation does not.** Five reviews each found at least one, all of
the same species — uncertainty or failure quietly presenting as a confident
success:

- a test asserting an arbitrary tie-break the specification never defined
- a parser filter that silently dropped requisites whose detail cell was blank
- a numeric coercion that turned unreadable credit points into a zero-credit subject
- an uninterpretable admission requisite reporting as satisfied
- area-of-study requisites misclassified as admission requisites, producing a
  confidently wrong "not met" against a student who did hold the major

Do not re-execute that plan expecting current behaviour; the corrections live in
the git history. The pattern is worth a paragraph in the final report in its own
right: for a project arguing that deterministic enforcement beats a probabilistic
model, the instructive failure mode was not the model being wrong — it was the
scaffolding around it being confidently wrong without saying so.
