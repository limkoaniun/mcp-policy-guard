# Demo: the upload screen (job 6, upload slice)

**Date:** 2026-09-28
**Status:** agreed in conversation; nothing built.
**Related:** `context/2026-09-23-pivot-upload-injection-vs-rag.md` §5 item 6
(the job), `context/2026-09-23-upload-channel-design.md` §3.4 (what approval
means on each arm), `context/report/section4-draft.md` §4.5 (the UI as the
report describes it), `src/enrolment/demo/` (what exists).

## 1. Scope

The chat page gains the upload channel. In this slice:

- an **attach control** offering the active student's document, clean or as
  received (planted footer);
- a **"what the model saw"** panel on every `get_attachment` row;
- an **approval card** for a `require_approval` verdict, with Allow and Refuse;
- an **eRequest log** panel under the live record;
- **student names** in the picker;
- removal of the 32513 description overlay and its intro copy.

Out of this slice, left for the next one: the RAG position of the mode
selector, and taint that carries across messages in a session. The
Enforcement on/off switch stays as it is.

The bounding rule from spec §8 holds: every number and every text the page
shows comes from a run directory the proxy wrote, or from the scenario files
the campaign ran. Nothing is computed for display.

## 2. Documents

`src/enrolment/demo/documents.py` builds, at app start, one document per
persona from the four upload-borne scenarios (A-01..A-04). For a scenario with
`payload.location: attachment`:

```python
class DemoDocument(BaseModel):
    id: str            # the scenario's attachment id, e.g. "rpl-template"
    name: str          # e.g. "RPL_application_template.txt"
    persona: str
    scenario_id: str
    clean_text: str    # the scenario's attachment text, byte-identical to the benign twin
    planted_text: str  # clean_text.rstrip() + "\n\n" + payload.text.strip()
    payload_text: str  # so the page can highlight the footer
    prompt: str        # the scenario's task.description, offered as a chip
```

`planted_text` is produced by the same rule `run_setup.write_attachments`
uses, so the demo attaches the bytes the campaign measured. The concatenation
is factored out of `write_attachments` into `run_setup.plant_payload(text,
payload_text)`, and both call it, so the rule lives in one place.

`GET /api/documents` returns the list. The page filters by the active persona.

## 3. Session state

`ChatSession` gains:

```python
self.attachments: dict[str, dict] = {}   # id -> {"name", "text", "variant"}
self.erequests: list[dict] = []          # accumulated across turns
```

- `PUT /api/chat/{id}/attachments` with `{"attached": [{"id": ..., "variant": "clean" | "planted"}]}`
  replaces the session's attachment list. Unknown id or a document that is
  not this persona's is a 400. The list persists across messages until
  changed; it survives `reset` (it is how the demonstration is staged, like
  the allowlist) and is dropped on a persona switch (the page re-attaches
  when the presenter asks).
- Each `send` writes `run_dir / "attachments.json"` as `{id: {name, text}}`
  when the list is non-empty and passes it to `write_task(attachments_path=)`.
  The server's `get_attachment` then serves exactly that text.
- After each `send`, the turn's `erequests.jsonl` (if any) is read and its
  records appended to `self.erequests`. Each record is the server's own line
  (`ts`, `student_id`, `type`, `text`, `attachment_ids`) plus `turn`.
- `reset` clears `self.erequests` along with the history and the record.
- `state_of` adds `"attachments"` (the list, each with full `text`, `name`,
  `id`, `variant`) and `"erequests"`.

The overlay is removed: `INJECTED_SUBJECT`, `INJECTED_TEXT`, the `overlay=`
argument to `ChatSession` in `new_session`, and `injected` in `/api/tools`.
`ChatSession` keeps its `overlay` parameter for now because `demo.session`
and tests may use it; only the demo app stops passing one.

`get_attachment` joins `TOOLS` (`side_effect: False`) and therefore
`READ_ONLY` and all three presets. The comment saying it is deliberately
absent is deleted.

## 4. Calls in the trace

`DemoCall` gains `tainted: bool = False`, read from the decision record.
`state_of` and the `done` event carry it. The `call` event from the agent
loop is unchanged; the page identifies a `get_attachment` row by tool name and
finds the document in the session's attachment list by the `attachment_id`
argument (matching id or name, case-insensitively, as the server does).

## 5. Approval

`POST /api/chat/{id}/approve` with `{"turn": int, "index": int}` names one
decision of one earlier turn. Preconditions, each a 400 with a reason:

- the turn exists and the index is in range;
- that decision's verdict is `require_approval` and it was not forwarded;
- the session is not mid-message.

The replay runs in `run_dir = root / f"turn-{turn:03d}-approval"`, seeded
from the session's current `students.json`, with the same `attachments.json`
copied in and the same `task.yaml` fields, through a proxy started with
`--approval-mode approve` and the session's current enforcement. No model is
involved. Through `connect_subprocess` the session issues, in order:

1. every forwarded call to an untrusted tool from the named turn that
   precedes the held call (in practice the `get_attachment` reads), so the
   proxy is tainted the way it was;
2. the held call, with its recorded arguments.

The fresh proxy would otherwise be untainted, the taint gate would not fire,
and the log would say `allow` by the allowlist — a record of an event that
did not happen. Re-reading first makes the replay honest: the decision logged
is `require_approval`, `approval_mode: approve`, `forwarded: true`.

The state the replay leaves behind becomes the session's `students.json`; its
`erequests.jsonl` is appended to the session log with `turn` set to the turn
number and `confirmed: true`. The response is `state_of(live)` plus
`"confirmation"`: the `DemoCall` for the replayed held call, and the number of
re-read calls made (the page shows both, so nobody is shown one call when two
ran).

The `decisions` list returned to the page for the original turn is not
rewritten. The page appends a confirmation row after the held decision.

**Refuse** is page-only. Nothing ran and nothing is logged; the card reads
"refused by the student". Spec §3.4's "labelled as a confirmation in the
trace" applies to Allow.

## 6. Names

`data/personas.json` gains `"name"` on each record: Jane Doe S9000001, Omar
Haddad S9000002, Priya Nair S9000003, Tom Becker S9000004. `Student` ignores
unknown fields, so the validators and the server are unaffected; the tools
never return the name. `/api/personas` adds `"name"`.

## 7. The page

`frontend/src/App.jsx`, `api.js`, `styles.css`.

- **Picker:** `Jane Doe · S9000001 · Cybersecurity`.
- **Composer:** an **Attach** button opens a small menu listing the student's
  document twice — "as received (contains a planted footer)" and "clean copy".
  Choosing one calls the attachments endpoint. Attached documents appear as
  chips above the composer with an × to remove; the chip names the variant.
  Only one variant of a document can be attached at a time.
- **Suggestions:** the fixed three stay; a fourth chip is the attached
  document's scenario prompt, shown only while that document is attached.
- **Thread:** a `get_attachment` row shows the file name in place of raw
  arguments and, beneath it, a collapsed "what the model saw" panel with the
  document's full text. The payload text, when present, is highlighted. The
  `poison` message type is removed.
- **Tags:** *ran*, *stopped*, *needs approval* (verdict `require_approval`
  and blocked). With enforcement off, a `require_approval` verdict that was
  forwarded reads *ran*, and the decision row says it would have needed
  approval.
- **Decision log:** a `require_approval` decision shows the reason and, if it
  was blocked, an **approval card** with Allow and Refuse. Allow calls the
  approve endpoint, disables the card, and appends a row: "Confirmed by the
  student. Re-read N document(s), then forwarded." with the replayed call's
  verdict. Refuse marks the card "refused by the student". Tainted decisions
  carry a marker: *after untrusted read*. The Allowed/Denied heading becomes
  a three-way label: Allowed / Denied / Needs approval.
- **eRequest log:** a new card under the record. One entry per request:
  type, text (full, in a scrolling box), cited attachment ids, and
  "confirmed" when the entry came from an approval. Empty state: "No
  eRequests submitted."
- **Intro copy:** the 32513 sentence goes. Replacement: "Documents the
  student attaches are read through `get_attachment`, which the policy pack
  labels untrusted."
- **Persona switch:** attachments are not carried; the eRequest log clears.

## 8. Testing

Pytest, in `tests/test_demo_chat_api.py` and a new `tests/test_demo_documents.py`,
following the existing scripted-`ChatReply` pattern (no daemon):

- documents: one per upload persona; `planted_text` ends with the payload;
  `clean_text` equals the benign twin's attachment text;
- attach, then a scripted `get_attachment` call: the call's result contains
  the document text, and `turn-001/attachments.json` holds it;
- attach with an unknown id or another persona's document: 400;
- a scripted `submit_erequest` shows up in `state.erequests` with its type
  and text; `reset` clears it;
- approve on a held call (attach planted, scripted `get_attachment` then
  `drop` for Tom): the response carries a confirmation whose verdict is
  `require_approval`, and the record changed; the replay's decisions log
  shows `approval_mode: approve`;
- approve on a decision that was not held: 400;
- `/api/tools` lists `get_attachment` and carries no `injected` key;
- `/api/personas` carries names.

The page has no test runner. It is built with `npm run build` in `frontend/`
and checked in Chrome against the demonstration script in §4.5 (Jane Doe,
RPL template as received, enforcement off then on). The built files under
`src/enrolment/demo/static/` are committed, as before.

## 9. Docs to touch when built

`context/report/section4-draft.md` §4.5 "Currently built" line;
`context/2026-09-23-pivot-upload-injection-vs-rag.md` §5 (job 6 partly
done); `src/enrolment/demo/app.py` module docstring.
