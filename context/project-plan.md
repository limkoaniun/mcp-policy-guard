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
| Dataset build pipeline and coverage gate | ✅ Complete, **not yet run** |
| Synthetic student generator | ✅ Complete |
| Compliance scenario generator | ✅ Complete |
| **MCP proxy and policy engine** | **Next.** `proxy.py` is still a stub |
| Enrolment MCP server | Not started |
| Agent harness (Ollama) | Not started |

### Weeks 8–10 — experiments

Adversarial scenarios (20–25) and compliance scenarios (generated, hundreds);
runs with enforcement on and off; attack success rate, rule violation rate, task
completion, overhead.

### Weeks 11–13 — analysis and write-up

Ablations of individual policies; the prompting baseline comparison;
demonstration frontend; final report.

---

## Immediate next steps, in order

1. **Run the thin-slice build.** `python -m enrolment.build --scope thin`, roughly
   60 requests. Held pending confirmation of the access question (§5 of the spec).
   Everything downstream is currently untested against real data.
2. **Email the supervisor.** One message covers the five open questions in §10 of
   the spec, and the data-access confirmation. Any answer unblocks work.
3. **Start the literature review.** It is the one Weeks 1–3 deliverable still
   outstanding, and it is on the critical path for the report rather than the code.
4. **Specify and build the proxy.** The next spec; `proxy.py` has been a stub since
   the scaffold.

---

## Follow-on specifications

Each gets its own spec → plan → implementation cycle, in this order:

1. **Proxy and policy engine** — completes the vertical slice
2. **Enrolment MCP server and policy pack** — including the admission-descriptor
   normalisation decision and the session-load model the credit-point cap needs
3. **Adversarial attack suite**
4. **Evaluation harness and metrics**
5. **Demonstration frontend**

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
