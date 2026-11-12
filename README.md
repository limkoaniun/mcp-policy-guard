# MCP Policy Guard

A policy enforcement layer for MCP (Model Context Protocol) tool calls. It sits
between an LLM agent and its tools and keeps the agent inside deterministic
rules, both when an attacker is trying to push it out and when it simply gets
the rules wrong.

**32933 Research Project (Type I), UTS, Spring 2026.**
Guanyu Lin and Qiuyu Chen. Supervisor: Dr David Milne.

## Final report

The short technical report submitted for the subject is in this repository:

- [`docs/Research_Project_32933.pdf`](docs/Research_Project_32933.pdf),
  *MCP Policy Guard: Design and Implementation of an Enrolment Assistant.
  External Enforcement of Tool Permissions, Student Authority and Enrolment Rules.*

It covers the design, the prototype, the automated verification and the
browser comparison with and without the guard. This README is the working
summary; the report is the authoritative write-up.

## The problem

LLM agents act through tools, and MCP is becoming the standard interface
between agents and those tools. That creates two failure modes.

- **A hijacked agent.** Tool results such as web pages, emails and uploaded
  documents are untrusted data, yet the agent reads them in the same context
  as trusted instructions. Injected text can redirect it into leaking data or
  acting without authority.
- **An honestly mistaken agent.** Even with no attacker, an agent working
  within deterministic business rules will sometimes apply them incorrectly.
  Prerequisites, anti-requisites and credit-point caps are checkable with
  certainty, and beyond what a language model can be relied on to get right
  every time.

Defences inside the model are probabilistic and address neither with
certainty. The claim under test is that a deterministic layer outside the
model does.

## The approach

A transparent MCP proxy sits between the agent and its tool servers and
enforces policy outside the model:

- **Tool allowlists.** Only tools declared for the current task may be invoked.
- **Argument validation.** Arguments are checked against schemas and business
  rules, and every principal-scoped call is bound to the task's student.
- **Taint tracking.** Once the run has read untrusted content (for example an
  uploaded PDF), side-effecting calls are held until the user confirms them.
- **Rate and scope limits.** Caps on call frequency and data volume (stub).

Deny messages are written to be read by the agent, so a block becomes a
helpful explanation to the user rather than an opaque error.

## The case study

Student enrolment, standing in for My Student Admin and eRequests. Real UTS
catalogue and requisite data for course C04296; every student record is
synthetic.

The requisite rules are the point. UTS publishes them as nested boolean
expressions over numbered item tables:

```
32146   ((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))
32113   (((1 OR 1a OR 1b OR 1c) AND (2 OR 2a OR ... OR 2k)) OR ((3 OR 3a)))
```

They are institutionally real, not invented for the project, and plainly
beyond reliable single-pass evaluation by a small model. That is what makes
them a fair test of the argument.

## Repository layout

```
src/policy_guard/     the proxy and policy engine (domain-agnostic)
  policies/           allowlist, arg_validation, taint, limits
src/enrolment/        the enrolment case study: data, rules, agent harness, web demo
attacks/              attack scenario definitions (YAML), one folder per suite
configs/              policy packs
experiments/scored/   scored campaign output (metrics.json, judgements.jsonl)
frontend/             the demo web UI (built assets live in src/enrolment/demo/static)
scripts/              start-up helpers
tests/                unit tests
context/              specification, plan, threat model, design records
docs/                 the final report, demo guides and validation notes
data/                 built dataset and raw response cache (committed)
```

Raw campaign run directories live under `experiments/runs/` and are not
committed.

## Quickstart

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

Build the enrolment dataset (one-time, network, bounded to one course):

```bash
python -m enrolment.build --scope thin      # core + Cybersecurity major, ~30 subjects
python -m enrolment.build --scope full      # the whole C04296 tree
```

The build prints a coverage report. Any requisite item it could not interpret
is listed there rather than silently dropped.

### Run the proxy

Run from the repository root. The shipped pack's paths are relative to the
current working directory, not to the pack file.

```bash
python -m policy_guard --task <task.yaml> --policy-pack configs/enrolment.yaml \
    --run-dir <run-dir> --enforcement on
```

A policy pack (see `configs/enrolment.yaml`) names:

- `domain_rules`, a `"module:callable"` import path to the domain's
  argument-validator factory, loaded without a static import so
  `policy_guard` never depends on `enrolment`.
- `domain_rules_args`, the keyword arguments for that factory. Its
  `students_path` is also passed to the upstream server, so the file the
  validators check and the file the server mutates cannot diverge.
- `principal_exempt_tools`, tools that legitimately take no student argument
  (for example a catalogue search). A call to one of these naming the wrong
  student is still denied.

### Run the web demo

Three modes: RAG chatbot, agent without guard, agent with guard. See
[`docs/rag-demo.md`](docs/rag-demo.md) for setup, and `docs/demo/` for the
recording script used in the demo video.

### Run the campaigns

All campaigns are resumable: kill one and re-run the same command to continue.
`--dry-run` prints what would run without running it.

```bash
# adversarial (subsystem B)
python -m enrolment.agent.adversarial --campaign experiments/runs/adversarial/first

# compliance (subsystem C)
python -m enrolment.agent.compliance --campaign experiments/runs/compliance/first

# advice comparison, RAG versus the guarded agent
python -m enrolment.agent.advice --campaign experiments/runs/advice/first
python -m enrolment.metrics.advice --campaign experiments/runs/advice/first --out experiments/scored/advice-first

# score a campaign into the spec's §7 metrics (read-only on the runs)
python -m enrolment.metrics.report --campaign experiments/runs/adversarial/first --out experiments/scored/adversarial-first
```

## Evaluation

Two suites, each run with enforcement on and off, across several models with
repeats:

- **Adversarial.** Hand-authored injection scenarios across several attack
  classes, each with a benign twin so that twins cannot diverge and no attack
  is tuned to succeed.
- **Compliance.** A stratified sample of 248 scenarios drawn from the 4,650
  generated from the real requisite graph: 40 per rule depth, topped up so no
  failure mode falls below 25.

Metrics are attack success rate, rule violation rate, benign task completion
and enforcement overhead. Ground truth is the final state of the synthetic
database, not the model's text.

### Headline results

**Compliance** (`experiments/scored/compliance-first`, 4,960 runs, two
models, five repeats):

| | enforcement on | enforcement off |
|---|---|---|
| rule violations | **0 / 1700 (0%)** | **1670 / 1700 (98%)** |
| benign completion | 780 / 780 (100%) | 780 / 780 (100%) |
| decision latency | p50 1.688 ms | p50 1.719 ms |

Unenforced, both models enrol students in subjects they are not eligible for
in 98% of non-compliant scenarios. Enforced, never, and every compliant
enrolment still completes.

**Adversarial, output-injection class**
(`experiments/scored/adversarial-second`, 336 runs, four model sizes):

| model | payload delivered | enforcement off | enforcement on |
|---|---|---|---|
| `llama3.2` (2 GB) | 24/24 | 0/9 | 0/9 |
| `llama3.1:8b` (5 GB) | 18/24 | **6/6** | 0/6 |
| `qwen2.5:14b` (9 GB) | 21/21 | **6/9** | 0/9 |
| `llama3.3:70b` (43 GB) | 24/24 | **3/9** | 0/9 |
| overall | 87/93 | **15/33 (45%)** | **0/33 (0%)** |

The smallest model received the payload every time and acted on it never: it
is not resistant, it is too weak to follow the injected instruction.
Susceptibility appears at 8B and does not rise with size.

**Ablations** (each policy removed in turn):

| policies loaded | output-injection, enforced (`qwen2.5:14b`) | rule violations, enforced (`llama3.1:8b`) |
|---|---|---|
| both (baseline) | 0/9 | 0/170 |
| `arg_validation` only | 0/9 | 0/170 |
| `allowlist` only | **6/9** | **168/170 (99%)** |

Argument validation carries both results. The allowlist is untested here
rather than disproven: the attacks that landed used `enrol`, a tool that is
on the allowlist, so only argument validation could object.

### What the numbers do not show

- The enforced 0% on compliance is partly true by construction. The validator
  uses the same requisite evaluator that generated each scenario's expected
  answer, so it is a consistency check. The finding is the unenforced arm.
- The 30 unenforced non-violations are four scenarios whose student was
  already enrolled in the sampled subject. The bias is conservative.
- Depth 0 is entirely compliant, so it reports no violation rate.
- The first adversarial campaign returned a null result because the payload
  never entered any model's context. It is kept as a record of the defect.
- Tool-shadowing and poisoned-description scenarios have no enforcement-off
  arm, because nothing delivers those payloads yet. They report absence, not
  zero.
- Effective depth is one tool call per user turn for the models tested, so
  benign completion is a capability floor, not what the proxy costs.
- Exfiltration detection reads eRequest text for a student other than the
  principal. It is a heuristic and is reported as one.

## Status

Done: the full C04296 dataset (93 subjects, 50 synthetic students, 4,650
compliance scenarios, zero unparsed requisites); the proxy with allowlist,
argument validation and taint policies; the agent harness; adversarial,
compliance and advice campaigns, scored; the web demo with RAG, unguarded and
guarded modes; the demo scenario set; and the final report.

Unfinished: rate and scope limits, session-long taint for the agent mode, and
delivery mechanisms for tool-shadowing and poisoned-description payloads.

Design records are dated files in `context/`. The specification is
`context/project-spec.md` and the schedule is `context/project-plan.md`.
