# MCP Policy Guard

A policy enforcement layer for MCP (Model Context Protocol) tool calls that keeps
LLM agents inside deterministic rules — both when an attacker is trying to push
them out, and when they simply get the rules wrong.

**32933 Research Project (Type I), UTS, Spring 2026 — Guanyu Lin, Qiuyu Chen**
Supervisor: David Milne

## The problem

LLM agents act through tools, and MCP is becoming the standard interface between
agents and those tools. That creates two failure modes.

**A hijacked agent.** Tool results — web pages, emails, subject descriptions — are
untrusted data, yet the agent processes them in the same context as trusted
instructions. Injected text can redirect it into exfiltrating data or acting
without authority.

**An honestly mistaken agent.** Even with no attacker, an agent asked to work
within deterministic business rules will sometimes apply them incorrectly.
Prerequisites, anti-requisites and credit-point caps are checkable with certainty,
and beyond what a language model can be relied on to get right every time.

Defences inside the model are probabilistic and address neither with certainty.
The claim under test is that a deterministic layer outside the model does.

## The approach

A transparent MCP proxy sits between the agent and its tool servers and enforces
policy outside the model:

- **Tool allowlists** — only tools declared for the current task may be invoked
- **Argument validation** — arguments checked against schemas and business rules
- **Taint tracking** — content from untrusted tools is marked; side-effectful calls
  influenced by tainted context require explicit approval
- **Rate & scope limits** — caps on invocation frequency and data volume

Deny messages are written to be read by the agent, so a block becomes a helpful
explanation to the user rather than an opaque error.

## The case study

Student enrolment, standing in for My Student Admin and eRequests. Real UTS
catalogue and requisite data for course C04296; every student record synthetic.

The requisite rules are the point. UTS publishes them as nested boolean
expressions over numbered item tables:

```
32146   ((1 AND (2 OR 2a OR 2b)) OR (3) OR (4) OR (5) OR (6))
32113   (((1 OR 1a OR 1b OR 1c) AND (2 OR 2a OR ... OR 2k)) OR ((3 OR 3a)))
```

Institutionally real, not invented for the project, and plainly beyond reliable
single-pass evaluation by a small model — which is exactly what makes them a fair
test of the argument.

## Evaluation

Two suites: adversarial scenarios across four attack classes, and a business-rule
compliance suite generated from the real requisite graph rather than hand-written.
Metrics: attack success rate, rule violation rate, benign task completion, and
enforcement overhead, each with enforcement on and off, across two models with
repeats.

Ground truth is the final state of the synthetic database, not the model's text.

See `context/project-spec.md` for the full design.

## Repository layout

```
src/policy_guard/     the proxy and policy engine (domain-agnostic)
  policies/           individual policy modules
src/enrolment/        the enrolment case study: data acquisition and rule evaluation
attacks/scenarios/    attack scenario definitions (YAML)
experiments/          evaluation harness and metrics
tests/                unit tests
context/              specification, plan, threat model, design records
data/                 built dataset and raw response cache (created by the build, committed)
```

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

The build prints a coverage report. Any requisite item it could not interpret is
listed there rather than silently dropped, and gates widening the fetch.

Run the proxy in front of the enrolment MCP server, from the repository
root (the shipped pack's `upstream_args`, `domain_rules_args` and `--students`
are relative paths resolved against the current working directory, not
against the pack file's location):

```bash
python -m policy_guard --task <task.yaml> --policy-pack configs/enrolment.yaml \
    --run-dir <run-dir> --enforcement on
```

A policy pack (e.g. `configs/enrolment.yaml`) may name `domain_rules` — a
`"module:callable"` import path to a domain's argument-validator factory,
resolved by `policy_guard/__main__.py` without a static import — and
`domain_rules_args`, the keyword arguments passed to that factory alongside
the run directory. This is the pack's single source of truth for where a
domain's data lives: `domain_rules_args.students_path`, in the shipped pack,
is also read by `__main__.main` and appended as the upstream server's own
`--students` argument, so the file the proxy's validators check a student's
enrolment against and the file the upstream server actually mutates cannot
diverge. A pack may also name `principal_exempt_tools` — tools that legitimately
take no principal argument (e.g. a catalogue search with no student to bind
to) and so are not denied merely for omitting it; a call to one of these
naming the *wrong* principal is still denied unconditionally, and any tool
left off the list stays enforced by default.

Run the adversarial campaign (subsystem B). Resumable: kill it and re-invoke
the same command to continue.

```bash
python -m enrolment.agent.adversarial --campaign experiments/runs/adversarial/first --dry-run
python -m enrolment.agent.adversarial --campaign experiments/runs/adversarial/first
```

## Status

**Done.** The full C04296 dataset is built and committed — 93 subjects, 201
cached pages, 50 synthetic students and 4,650 compliance scenarios, with zero
unparsed requisites and zero unreadable rules. Rebuilds from cache in 0.24s. And
the data layer behind it: requisite rule parser and evaluator, catalogue
and requisite extractors, cached fetcher with provenance, build pipeline with
coverage gate, synthetic student generator, and the compliance scenario generator.

The MCP proxy and policy engine are runnable end to end: the enrolment MCP
server, the transparent proxy (`python -m policy_guard`), and two policies —
the tool allowlist and argument validation, which binds every principal-scoped
call to the task's principal (exempting the tools the pack declares as
legitimately not principal-scoped) and enforces requisites, anti-requisites
and the credit-point cap via `enrolment.policy_pack:build_validators`, loaded
by import path so `src/policy_guard/` never imports `enrolment` directly.

The adversarial scenario suite exists and replays against the real proxy:
sixteen hand-authored scenarios (`attacks/scenarios/`, schema in
`src/enrolment/scenarios_spec.py`, runner in
`src/enrolment/scenario_runner.py`) — eight injected across the four attack
classes and their eight benign twins — run against `configs/enrolment-personas.yaml`.
395 tests. Three limitations of that suite as it stands, stated plainly rather
than left implicit:

1. Half the injected suite (SC-01, SC-03, SC-05, SC-08) is marked
   `deliverable: false`, because nothing yet delivers a tool-metadata payload
   or registers a decoy tool. Those four have no enforcement-off arm.
2. No scenario demonstrates an attack *succeeding*. There is no agent harness
   yet, so every enforcement-off arm goes unrun; the suite currently measures
   that the proxy denies the right calls for the right reasons, not that
   those calls would otherwise have done damage.
3. The exfiltration class is undefended in its canonical form: SC-07's third
   call, a `submit_erequest` carrying another student's record under the
   *correct* principal, is allowed. Principal binding does not apply because
   the principal is right, and the `taint` and `limits` policies that would
   catch it are one-line stubs outside the shipped pack.

**The agent loop exists** — the first of the four subsystems the agent harness
needs (`context/2026-09-02-agent-loop-design.md`). `src/enrolment/agent/loop.py`
drives a model through an MCP conversation against the real proxy and records
every response and attempted call, without repairing the model's mistakes;
`src/enrolment/agent/ollama.py` is the only module that knows Ollama's HTTP API
exists, and the model is otherwise an injected callable, so the loop is
exhaustively testable with no daemon running. 436 tests pass in total.

Measured against both candidate models, effective depth is one tool call per
user turn — neither recovers from an error within a turn. Benign task
completion therefore has a ceiling set by model capability, not by
enforcement; that ceiling applies identically whether enforcement is on or
off, so the on/off comparison still holds, but the absolute completion number
must be read as a capability floor, not as what the proxy costs.

**Subsystem B exists** — adversarial runs
(`context/2026-09-02-adversarial-runs-design.md`).
`python -m enrolment.agent.adversarial --campaign <dir>` drives the sixteen
scenarios through a real model across two models, five repeats and both
enforcement arms: 280 runs, resumable, indexed. It drives and records; it does
not judge. Turn scripts are derived from each scenario's own description
rather than authored, so twins cannot diverge and no attack was tuned to
succeed.

Two of the four attack classes have an enforcement-off arm. `tool-shadowing`
and `poisoned-description` remain `deliverable: false` — nothing patches tool
metadata or registers a decoy tool — so their four scenarios report the
enforcement-on arm only, and the results table says so rather than omitting
them.

**Next.** The remaining policies (taint tracking, rate & scope limits), the
two missing payload-delivery mechanisms, and subsystems C (compliance runs)
and D (metrics and ablations). Until D lands, no metric from
`context/project-spec.md` §7 can be reported.

**Outstanding from Weeks 1–3.** The literature review.

Current direction lives in `context/project-spec.md`; schedule and build order in
`context/project-plan.md`.
