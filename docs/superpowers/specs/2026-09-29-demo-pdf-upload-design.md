# Demo: PDF documents, dragged onto the chat

**Date:** 2026-09-29
**Status:** built 2026-09-29; see the plan of the same name under docs/superpowers/plans/.
**Supersedes:** the attach menu of `docs/superpowers/specs/2026-09-28-demo-upload-screen-design.md`
§2–§3 and §7 "Composer". Everything else in that spec stands.
**Related:** `context/2026-09-23-pivot-upload-injection-vs-rag.md` (future work
named "documents beyond plain text (PDF extraction as its own attack
surface)"), `src/enrolment/demo/` (what exists).

## 1. Scope

The presenter drags a file onto the chat, or picks one with the Attach
button. The page uploads it. The server pulls the text out and adds it to the
session's attachments, where `get_attachment` serves it exactly as today. The
four demo documents ship as real PDFs, clean and as received, with the planted
footer hidden as white 1pt text.

In this slice:

- a **build script** that writes the eight PDFs from the scenario files;
- an **upload endpoint** that accepts PDF and plain text, extracts the text,
  keeps the file, and tags an upload that matches a shipped document;
- the **page**: drop zone, file picker, chips, the scenario prompt chip and
  the footer highlight driven by the server's tag;
- removal of the **attach menu** and its endpoint.

Unchanged: the proxy, the pack, `get_attachment`, the per-turn
`attachments.json`, the approval replay, the eRequest log, the campaign.

Out of scope: OCR of scanned PDFs (an upload with no extractable text is
refused with a reason); documents beyond PDF and plain text; the RAG mode;
session-long taint.

## 2. Whitespace, stated up front

PDF text extraction does not return the bytes that went in: line breaks and
runs of spaces come back differently. So the text the demo serves for a
shipped PDF equals the scenario's text **up to whitespace**, not byte for
byte. The test in §8 pins that, and `context/report/section4-draft.md` §4.5
gets one sentence saying so. The campaign itself still runs on the scenario
text; only the demo reads PDFs.

Normalisation, used everywhere this spec says "matches":
`" ".join(text.split())`.

## 3. Shipped documents

`python -m enrolment.build_documents` writes, for each scenario with
`payload.location: attachment` and `deliverable: true` (A-01..A-04):

| path | content |
|---|---|
| `data/documents/clean/<name>.pdf` | the attachment text |
| `data/documents/as-received/<name>.pdf` | `run_setup.plant_payload(text, payload.text)` with the payload part in white, 1pt |

`<name>` is the scenario attachment's `name` with `.txt` replaced by `.pdf`
(`RPL_application_template.pdf`, `transfer_checklist.pdf`,
`group_agreement.pdf`, `medical_letter.pdf`). Body: Helvetica 11pt, one
`multi_cell` per line so line structure survives. The footer: after a blank
line, `set_text_color(255, 255, 255)`, `set_font_size(1)`, the payload text.
All four texts are Latin-1 (checked), so the core font suffices; a non-Latin-1
character makes the script fail loudly rather than write a mojibake PDF.

The PDFs are committed. The script is idempotent and deterministic enough that
re-running it and diffing is the way to check they are current; the test in
§8 checks the round trip, not the bytes. fpdf2 is a dev dependency (only the
script needs it); pypdf is a runtime dependency (the server reads uploads).

## 4. Upload

`POST /api/chat/{id}/upload`, multipart, field `file`.

- Accepted: `.pdf` (`application/pdf`, or the `%PDF-` magic) and `.txt`
  (decoded UTF-8, `errors="replace"`). Anything else: 400 naming the type.
- Size: 2 MB. Over: 413.
- Extraction: pypdf, `"\n".join(page.extract_text() or "" for page in pages)`.
  A result that is empty after `strip()`: 400 "no text could be read from
  this PDF (a scanned document needs OCR, which the demo does not do)".
- Id: the file's stem, lower-cased, runs of non-alphanumerics collapsed to
  `-` (`RPL_application_template.pdf` → `rpl-application-template`). A second
  upload with the same id replaces the first.
- The original bytes are kept at `<session root>/uploads/<id>/<name>` as
  evidence; nothing reads them back.
- The attachment entry becomes `{"name", "text", "kind": "pdf"|"txt",
  "known": null | {...}}`. `_write_attachments` keeps writing only `name` and
  `text` to `attachments.json`, so the tool and the replay see what they saw
  before.
- **Known-document tag.** If the normalised text equals a shipped document's
  normalised planted text, or clean text, the entry carries
  `known = {"scenario_id", "document_id", "variant": "planted"|"clean",
  "prompt", "payload_text"}`. `prompt` is the scenario's task description with
  the scenario document's name replaced by the uploaded file's name, so the
  chip reads "…I've attached the application template I found,
  RPL_application_template.pdf…". `payload_text` is present only for
  `planted`.
- Response: `state_of(live)`.

`DELETE /api/chat/{id}/attachments/{attachment_id}` removes one (the chip's
×). `PUT /api/chat/{id}/attachments` and the `variant` field are removed.
`GET /api/documents` stays, as the source of the known-document texts for the
server and of nothing for the page.

Attachments still survive `reset` and are dropped on a persona switch. The
uploads directory is left on disk like turn directories are.

## 5. The page

- **Drop zone.** The chat card. On `dragenter`/`dragover` with files it gets a
  `dropping` class and an overlay reading "Drop to attach". On `drop`, each
  file is uploaded in turn; the chip appears when the server answers. While
  `busy`, drops are ignored and the overlay does not show.
- **Attach button.** A hidden `<input type="file" accept=".pdf,.txt" multiple>`
  the button clicks. The menu is deleted.
- **Chips.** `📎 name · PDF` or `· text`; `· as received (contains a planted
  footer)` is appended when `known.variant === "planted"`, `· matches the demo
  document` when `clean`. × calls the delete endpoint.
- **Suggestions.** The fixed three, plus `known.prompt` for each attached
  document that has one.
- **"What the model saw".** Unchanged, except the highlight uses the row's
  snapshot of `known.payload_text` rather than a lookup by document id.
- **Errors.** A refused upload shows the server's `detail` in the thread's
  system bubble, like other errors.

## 6. Build script location and naming

`src/enrolment/build_documents.py`, run as `python -m enrolment.build_documents
[--out data/documents]`. It imports `documents_from` and `plant_payload`; it
does not restate either. `demo/documents.py` is unchanged: the server's tag
builds the prompt by replacing the scenario document's `name` with the
uploaded file's name, so no `.pdf` name needs storing.

## 7. Dependencies

`pyproject.toml`: `pypdf>=4` in `dependencies` with a comment (the demo reads
uploaded PDFs); `fpdf2>=2.7` in `dev` with a comment (only the document build
script). `python-multipart` is already installed as a FastAPI dependency;
declare it explicitly, since the upload route needs it.

## 8. Testing

Pytest, no daemon:

- `tests/test_build_documents.py`: the script writes eight files into a
  `tmp_path`; for each, pypdf's text normalised equals the scenario's
  clean or planted text normalised; the as-received PDF's raw content stream
  contains a `1 Tf`-sized text object and a white fill (`1 1 1 rg`) — one
  assertion each, so a future change that makes the footer visible fails.
- `tests/test_demo_chat_api.py`: upload the shipped as-received RPL PDF for
  Jane → the attachment carries `known.variant == "planted"` and a prompt
  naming the `.pdf`; a scripted `get_attachment` returns text containing the
  footer; upload a `.txt` → `kind == "txt"`, `known` null; upload a `.png` →
  400; upload 2 MB + 1 → 413; a PDF with no text → 400; DELETE removes the
  chip's attachment; approve still works after an upload (Tom's checklist
  as received, `drop` held, Allow replays).
- `tests/test_demo_chat.py`: `_write_attachments` writes only `name` and
  `text` even when the entry carries `kind` and `known`.
- Chrome, by the controller: drag `data/documents/as-received/RPL_application_template.pdf`
  onto the chat; chip and prompt chip appear; send; the model reads it; the
  panel shows the footer highlighted; the approval card works.

## 9. Docs to touch when built

`context/report/section4-draft.md` §4.5 (the composer's attach control is a
drop zone and file picker; the shipped PDFs; the whitespace sentence);
`context/2026-09-23-pivot-upload-injection-vs-rag.md` §4 future work (PDF
extraction is now in the demo; OCR and other formats remain future work);
`README.md` if it lists how to run the demo (mention `data/documents/`).
