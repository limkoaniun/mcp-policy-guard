# Pivot: upload-borne injection, compliance, and a RAG baseline (supervisor feedback, 2026-09-23)

**32933 Research Project (Type I) · UTS · Spring 2026**
**Supersedes, in part:** `context/project-spec.md` §1–§3 (rewritten the same
day); §7 and §8 still describe the previous design and are to be revised.
**Related:** `context/2026-09-14-rag-baseline-design.md` (the RAG arm, parked),
`context/report/section4-draft.md` (rewritten for the pivot),
`context/threat-model.md` (taint design), `attacks/README.md` (the old channel).

---

## 1. The feedback, as given

1. The research question changes. The guarded agent is compared **against a
   RAG chatbot**. Both halves of the old question stay — attacks and benign
   rule violations — but the attack half changes shape (point 3).
2. The motivating example — hidden text planted in a subject description —
   is not plausible. The handbook is university-published; "poison or
   mistake" there is impossible. Drop that scenario.
3. The attack is real and stays. The plausible channel is **what students
   upload**: Special Consideration and Recognition of Prior Learning
   submissions, or another channel if a better one is found.
4. Report §4 (Design) needs three additions: codebase architecture, UI
   design, and named personas as part of the business logic.

## 2. The question, before and after

Before:

> …prevent (a) adversarial prompt-injection attacks and (b) benign violations
> of enrolment business rules, while preserving the agent's ability to
> complete legitimate tasks?

After:

> To what extent can a rule-based policy enforcement layer, interposed between
> an LLM agent and its MCP tool servers, prevent (a) prompt-injection attacks
> carried in documents students upload and (b) benign violations of enrolment
> business rules — compared with a retrieval-augmented (RAG) chatbot — while
> preserving the agent's ability to complete legitimate tasks?

## 3. What the pivot changes, and what it does not

**Unchanged.** The proxy, the policy engine, the gates that exist (allowlist,
principal binding, argument validation), the enrolment server's ten tools,
the dataset, the compliance suite, the compliance campaign and its scoring.
The mechanism is the same; the payload arrives through a different door and a
third arm is added to the comparison.

**Changed.**

| Before | After |
|---|---|
| 2×2: {adversarial, benign} × {on, off} | three arms — RAG · agent off · agent on — on both suites |
| payload delivered by a `description_suffix` overlay on a catalogue entry | payload delivered inside an uploaded document, read through a new untrusted tool `get_attachment` |
| attack classes: output-injection, poisoned-description, tool-shadowing, exfiltration | attack classes: rule-breaking action, out-of-task action, cross-principal action, exfiltration — all upload-borne; tool-shadowing and poisoned tool metadata move to future work |
| taint tracking: designed, stub, "not needed for the evaluated prototype" | taint tracking: **required** — it is the only gate that stops exfiltration into a permitted call |
| `submit_erequest(student_id, text)` | `submit_erequest(student_id, type, text, attachment_ids)` |
| enforcement on/off toggle in the demo | three-way mode selector (RAG · no guard · guard); attach control; approval card |
| personas are IDs | personas are named, each pinned to one rule and one document |
| attacker: unspecified "attacker" who edits the handbook | two attacker models: a third party who seeds a document the student uploads; the student who edits their own document to game a rule |

**Why the channel change costs little.** The proxy already models a tool
result as the attack surface and already labels tools by trust in the pack.
An uploaded document read through a tool is the same shape as a subject
description read through a tool; only the tool is new. The scenario loader's
`payload.location` gains a value (`attachment`) and the overlay mechanism
becomes the attachment store. The suite's twin structure (injected / benign,
differing only in the payload) carries over unchanged.

**What the channel change costs.** Taint tracking stops being optional. In
the old suite the exfiltration scenario was the one class the current gates
could not carry; under the new question exfiltration is the headline attack
(the record dump in the opening story), so gate 4 must exist before any
adversarial number is reported.

## 4. Consequences for the report

- §1–§2: both failure modes; the upload as the credible channel; RAG as the
  safe-but-useless pole.
- §4: rewritten — `context/report/section4-draft.md`, six subsections.
- §6 (results): the compliance campaign as it stands (violation rate,
  completion, overhead, by depth). The adversarial campaign re-run on the
  upload channel once it exists; the old runs cited only as gate evidence
  with the channel named. The RAG arm once built; until then, its
  by-construction zeros labelled as such.
- Future work: tool-shadowing and poisoned tool metadata (the suite's SC-01,
  SC-03, SC-05, SC-08); rate/scope limits; census dates; documents beyond
  PDF and plain text; OCR of scanned PDFs (the demo reads PDFs with pypdf and
  refuses one with no text).

## 5. Code work that follows

Job 5 is built and measured (`context/2026-09-23-rag-advice-design.md`; `experiments/scored/advice-first`). Job 7 is measured on the upload channel (`experiments/scored/adversarial-upload-second*`) and the prompted baseline is scored (`experiments/scored/compliance-told-the-rules`). Job 6 is built except the RAG position of the mode selector and session-long taint (`docs/superpowers/specs/2026-09-28-demo-upload-screen-design.md`); job 8 (spec §7–§8 rewrite) is open.

In the order that unblocks the report:

1. **Attachments.** `src/enrolment/attachments.py`: a per-session store of
   named text documents. New tool `get_attachment(student_id, attachment_id)`
   on the server, labelled `untrusted` in both packs. Scenario
   `payload.location: attachment` delivers a payload by planting it in a
   named document; `deliverable: true` for that location.
2. **Taint tracking** (`src/policy_guard/policies/taint.py`), per
   `context/threat-model.md`: per-turn untrusted-read flag from the pack's
   trust labels; an action call after an untrusted read is tainted; tainted
   + arguments carrying content not present in the principal's own messages →
   DENY; tainted otherwise → REQUIRE_APPROVAL. The batch harness scores
   REQUIRE_APPROVAL as denied; the demo shows an approval card. Add `taint`
   to `policies:` in both packs.
3. **Typed eRequests.** `submit_erequest(student_id, type, text,
   attachment_ids)`; the log records all four; no validator reads the text.
4. **Rewrite the attack suite** for the upload channel: four classes × the
   four personas' documents, each with a benign twin. Retire the overlay
   delivery. Move tool-shadowing / poisoned-description scenarios to
   `attacks/future/` with a README line.
5. **Build the RAG arm** from `context/2026-09-14-rag-baseline-design.md`,
   retrieving attachments alongside subjects and the record. Owner per that
   document: Qiuyu Chen.
6. **Demo.** Attach control; "what the model saw" panel for attachments;
   approval card; eRequest log panel; three-way mode selector; names in the
   picker (`name` field in `data/personas.json`: Jane Doe S9000001, Omar
   Haddad S9000002, Priya Nair S9000003, Tom Becker S9000004). Remove the
   32513 description overlay and its intro copy.
7. **Re-run the adversarial campaign** on the new suite; add the ablation
   arm `--ablate taint`. Re-score.
8. **Spec §7–§8** rewritten for three arms and the new channel.

Nothing above deletes the old scenarios or their scored runs.
