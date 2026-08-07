# Task: clean up scaffold, then implement the first working slice of MCP Policy Guard

## Context

This is a solo 13-week university research project (UTS 32933, Type I prototype).
The repo scaffold already exists: an MCP proxy that sits between an LLM agent and
its MCP tool servers and enforces deterministic policies to defend against
prompt-injection attacks. Read README.md and context/threat-model.md first — they
define the architecture and threat model. Do not restate them back to me.

Existing layout:
src/policy_guard/proxy.py PolicyProxy (stub)
src/policy_guard/engine.py PolicyEngine + Verdict enum (ALLOW/DENY/REQUIRE_APPROVAL)
src/policy_guard/policies/ allowlist, arg_validation, taint, limits (empty stubs)
attacks/ YAML attack-scenario format (see attacks/README.md)
experiments/run_eval.py evaluation harness (stub)
tests/

## Stack (already decided — do not switch)

- Python 3.11+, official `mcp` SDK, `pydantic` v2, `pyyaml`
- Local models via Ollama for the agent (later); NOT needed for this first slice
- `pytest` for tests, `ruff` for lint

## Step 1 — cleanup (do this first)

- Delete any stray files: a folder literally named `{src` if present, and
  experiments/run_eval.py (it will be recreated at the evaluation phase).
- Verify the tree matches README.md's "Repository layout" section; if any real
  file is missing, create it as a documented stub. Do NOT delete the policy
  stubs (arg_validation, taint, limits) — they are intentionally empty for now.
- Commit the cleanup separately before starting Step 2.

## Step 2 — scope of THIS session (keep it tight)

Build an end-to-end vertical slice that proves the architecture, using the
allowlist policy only. Do NOT implement taint tracking, arg validation, or
limits yet — leave those as stubs.

1. A minimal **mock MCP tool server** (stdio) under `src/policy_guard/mock_servers/`
   exposing 2–3 trivial tools (e.g. `read_file`, `send_email`, `list_files`) that
   just return canned strings. This is a test fixture, not a real integration.

2. The **PolicyProxy**: an MCP server to the agent side AND an MCP client to the
   upstream mock server, forwarding `list_tools` and `call_tool` through. Every
   forwarded `call_tool` must pass through `PolicyEngine.evaluate()` first. On a
   DENY verdict, return an MCP error result instead of forwarding, and log the
   decision.

3. The **PolicyEngine**: implement the combination logic described in engine.py's
   docstring (short-circuit on first DENY; REQUIRE_APPROVAL is sticky). ALLOW only
   if every policy allows.

4. The **allowlist policy**: implement `check(call, context)` so that only tools
   named in a per-task allowlist (loaded from a simple config) are permitted;
   everything else
