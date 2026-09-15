# Design: the RAG baseline mode (parked, not yet built)

**32933 Research Project (Type I) · UTS · Spring 2026**
**Related:** `context/2026-09-07-demo-chatbot-brief.md` (the demo this extends)
**Date:** 2026-09-14
**Status:** designed and agreed in conversation; nothing in this document has
been built. Implementation is owned by Qiuyu Chen — this document is the
handoff. The demo it extends runs with
`python -m uvicorn enrolment.demo.app:create_app --factory` from the repo root.

---

## 1. Why a RAG mode, and why not "off = RAG"

The supervisor asked for a RAG system to compare against the agentic design.
The first-proposed shape — make the demo's enforcement-OFF toggle *be* the RAG
system — was rejected on the demo brief's own principle: the toggle must
change exactly one thing. On vs off is a controlled comparison (same agent,
same attack, enforcement acting or not); replacing the off arm with a
different architecture would make the comparison uninterpretable, and the
report's §6 numbers are built on that controlled pair.

Instead the toggle becomes a **three-way mode selector**, and the RAG system
is a *third* arm:

| Mode | Can act? | Safe? |
|---|---|---|
| RAG chatbot | no — can only talk | trivially: there is nothing to hijack into acting |
| Agent, no guard (today's OFF) | yes | no — hijackable, breaks business rules |
| Agent + Policy Guard (today's ON) | yes | yes — deterministic enforcement |

The demo story this supports: *RAG is safe but cannot do the job; an agent
can do the job but is dangerous; the guard keeps the useful one safe.*

## 2. What RAG mode is

No agent loop, no MCP, no proxy, no tools. Per user message:

1. **Retrieve** — select relevant text deterministically (no model involved).
2. **Stuff** — place the retrieved text in the model's context.
3. **Answer** — one chat completion, no tool definitions offered.

New module: `src/enrolment/rag.py`. The model callable is injected the same
way as everywhere else in this repo (`ollama_chat` in production, a scripted
callable in tests), so the module is fully testable with no daemon.

## 3. Retrieval

Two sources, both the same data the agent's tools read — deliberately, so the
two systems see the same world and the comparison is fair:

- **Subjects:** the same keyword matching the `search_subjects` tool uses,
  over the built catalogue (`data/catalogue.json` + `data/requisites.json`).
  Reuse the server's matching logic rather than duplicating it; extract a
  shared helper if needed.
- **The student's record:** read-only, from the session's own students file.
  This keeps RAG competitive on personal advice questions ("what can I take
  next?"), so the comparison is not rigged against it.

No embeddings, no vector index. Retrieval is deterministic; the report should
describe it as "retrieval-augmented generation with a deterministic keyword
retriever" and not overclaim.

**The injection must survive retrieval.** The demo plants an injected payload
in subject 32513's description via the session overlay
(`description_suffix`). RAG retrieval must apply the same overlay, so the
poisoned description reaches the RAG model's context exactly as it reaches
the agent's. The expected observable: the RAG model may *say* compromised
things, but no state can change — the record panel never moves. The attack
surface is identical across all three modes; only the blast radius differs.

## 4. UI and API

- The Enforcement toggle in the demo page becomes a three-way selector:
  **RAG chatbot · Agent (no guard) · Agent + guard**.
- Session state: `mode: "rag" | "agent-off" | "agent-on"` replaces the
  boolean enforcement field. `PUT /api/chat/{id}/enforcement` becomes
  `PUT /api/chat/{id}/mode` (or the field generalises; pick whichever keeps
  the frontend diff smallest).
- In RAG mode the policy console shows "no tools in this mode — nothing to
  intercept" rather than an empty log; the tool checkboxes are inert.
- Replies are labelled with the driver, as everywhere else in the demo: a RAG
  answer is labelled as RAG + model name. The demo's honesty rules
  (`driver` labels, no scripted text presented as model output) carry over.
- The same model dropdown drives all three modes.

## 5. Out of scope

- The proxy, the policies, the scenario suite, both campaigns, and the
  metrics pipeline: untouched.
- No scored RAG campaign. A RAG bot makes zero tool calls, so its attack
  success and rule violation rates are 0 *by construction* and its action
  task completion is 0 by construction; that is an argument for the report's
  discussion section, not an experiment. (Revisit only if the supervisor
  wants a completion number for advice-only tasks.)

## 6. Testing

- Retrieval unit tests: given a fixed catalogue, a query returns the expected
  subjects; the overlay's poisoned description is present in retrieved text.
- RAG session tests with a scripted model callable: the reply is produced
  from retrieved context; no students-file write occurs (assert file bytes
  unchanged); the session records `driver` correctly.
- App tests: mode switching via the API; RAG mode refuses nothing but calls
  no tools; existing agent-mode tests unchanged.
