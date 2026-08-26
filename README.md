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

The proxy itself is not yet runnable — see below.

## Status

**Done.** The full C04296 dataset is built and committed — 93 subjects, 201
cached pages, 50 synthetic students and 4,650 compliance scenarios, with zero
unparsed requisites and zero unreadable rules. Rebuilds from cache in 0.24s. And
the data layer behind it: requisite rule parser and evaluator, catalogue
and requisite extractors, cached fetcher with provenance, build pipeline with
coverage gate, synthetic student generator, and the compliance scenario generator.
177 tests.

**Next.** The MCP proxy and policy engine — `src/policy_guard/proxy.py` is still a
stub. Then the enrolment MCP server, the remaining three policies, and the agent
harness.

**Outstanding from Weeks 1–3.** The literature review.

Current direction lives in `context/project-spec.md`; schedule and build order in
`context/project-plan.md`.
