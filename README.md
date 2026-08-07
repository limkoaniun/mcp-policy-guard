# MCP Policy Guard

A policy enforcement layer for MCP (Model Context Protocol) tool calls that defends
LLM agents against prompt-injection attacks.

**32933 Research Project (Type I), UTS, Spring 2026 — Guanyu Lin**
Supervisor: David Milne

## The problem

LLM agents act through tools, and MCP is becoming the standard interface between
agents and those tools. Tool results (web pages, emails, files) are untrusted data,
yet agents process them in the same context as trusted instructions. Injected
instructions hidden in tool outputs or poisoned tool descriptions can hijack an
agent into exfiltrating data or performing unauthorised actions.

## The approach

A transparent MCP proxy sits between the agent and its tool servers and enforces
deterministic policies, outside the model:

- **Tool allowlists** — only tools declared for the current task may be invoked
- **Argument validation** — tool arguments checked against schemas and scope rules
- **Taint tracking** — content from untrusted tools is marked tainted;
  side-effectful calls influenced by tainted context require explicit approval
- **Rate & scope limits** — caps on invocation frequency and data volume

## Evaluation

A purpose-built suite of 20–40 attack scenarios across four classes (instructions
injected into tool outputs, poisoned tool descriptions, tool shadowing, data
exfiltration via tool arguments), run against locally hosted open-weight models
(via Ollama). Metrics: attack success rate with/without enforcement, benign task
completion rate, enforcement overhead.

## Repository layout

```
src/policy_guard/     the proxy and policy engine
  policies/           individual policy modules
attacks/scenarios/    attack scenario definitions (YAML)
experiments/          evaluation harness and metrics
tests/                unit tests
docs/                 threat model, literature notes, design decisions
```

## Quickstart (target state)

```bash
pip install -e ".[dev]"
python -m policy_guard --config configs/default.yaml   # not yet implemented
pytest
```

## Status

Week 2–3: project setup, literature review, threat model. See docs/ for progress.
