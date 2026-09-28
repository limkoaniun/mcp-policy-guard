# Demo PDF Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The presenter drags a PDF (or .txt) onto the demo chat; the server extracts its text and serves it through `get_attachment`; the four demo documents ship as real PDFs with the planted footer hidden as white, tiny text.

**Architecture:** A small `demo/uploads.py` module owns what an upload is (accepted types, extraction with pypdf, the id rule, matching against the shipped documents). A build script `enrolment/build_documents.py` writes the eight PDFs with fpdf2 from the scenario files via the existing `documents_from` and `plant_payload`. The app gains `POST /api/chat/{id}/upload` and `DELETE /api/chat/{id}/attachments/{id}` and loses the attach-menu endpoint; the session's attachment entries gain `kind` and `known` but `attachments.json` keeps only `name` and `text`, so the proxy, the tool and the approval replay are untouched. The page swaps the menu for a drop zone and a file picker and reads the prompt chip and footer highlight off the server's `known` tag.

**Tech Stack:** Python 3.14, FastAPI + python-multipart, pypdf (read), fpdf2 (write, dev only), pytest (anyio auto), React 18 + Vite 5.

**Spec:** `docs/superpowers/specs/2026-09-29-demo-pdf-upload-design.md`

## Global Constraints

- Whitespace: extracted text equals scenario text **up to whitespace**; "matches" always means `normalise(a) == normalise(b)` with `normalise = lambda t: " ".join(t.split())`.
- Accepted uploads: `.pdf` (by `application/pdf`, or the `%PDF-` magic) and `.txt` (UTF-8, `errors="replace"`). Anything else: 400 naming the type. Over 2 MB (`2 * 1024 * 1024` bytes): 413. A PDF with no extractable text: 400 "no text could be read from … (a scanned document needs OCR, which the demo does not do)".
- Attachment id: the file's stem, lower-cased, runs of non-alphanumerics collapsed to `-`, stripped of leading/trailing `-` (`RPL_application_template.pdf` → `rpl-application-template`). A second upload with the same id replaces the first.
- The attachment entry is `{"name", "text", "kind": "pdf"|"txt", "known": None | {...}}`; `_write_attachments` writes only `name` and `text` to `attachments.json`.
- `known = {"scenario_id", "document_id", "variant": "planted"|"clean", "prompt", "payload_text"}`; `payload_text` present only for `planted`, and it is the footer **as it appears in the extracted text** so the page's `indexOf` highlight finds it; `prompt` is the scenario's task description with the scenario document's `name` replaced by the uploaded file's name.
- Shipped PDFs: `data/documents/clean/<name>.pdf` and `data/documents/as-received/<name>.pdf`, `<name>` = the scenario attachment name with `.txt` → `.pdf`. Body Helvetica 11pt; footer after a blank line in white (`255, 255, 255`) at `FOOTER_POINTS` (start at 1; the only allowed reason to raise it is that the round-trip test fails at 1).
- Refuse on the approval card stays page-only. Attachments survive `reset`, are dropped on a persona switch; the uploads directory is left on disk.
- Python: ruff line length 100; run tests with `.venv/bin/python -m pytest`. Comments explain why, in the voice of the existing modules. No TODO.
- Frontend: `cd frontend && npm run build` (node_modules present; no `npm install`); built files under `src/enrolment/demo/static/` are committed with the source.
- Dependencies: `pypdf>=4` and `python-multipart>=0.0.9` in `dependencies`; `fpdf2>=2.7` in `dev`. Install with `.venv/bin/python -m pip install -e '.[dev]'`.

## Review Focus

Inputs the spec implies but does not spell out, each pinned to a test in the task that owns the code:

1. Uploading the same file twice must replace the attachment, not duplicate it (Task 3, `test_uploading_the_same_file_again_replaces_it`).
2. A file whose name has spaces and capitals must still be readable by the tool by its exact file name (the server resolves by id or by name, case-insensitively) (Task 3, `test_a_file_name_with_spaces_resolves_by_name`).
3. A PDF with the wrong extension but the `%PDF-` magic must be accepted as a PDF (Task 1, `test_a_pdf_is_recognised_by_its_magic_even_with_another_extension`).
4. Allow must still work after an upload: the replay copies the turn's `attachments.json`, which now comes from an upload (Task 3, `test_approving_a_held_call_after_an_upload`).
5. The footer highlight must work on extracted text: `known.payload_text` must be a substring of the served `text` for the as-received PDFs (Task 3, `test_the_planted_footer_is_findable_in_the_extracted_text`).

---

### Task 1: Dependencies and the uploads module

**Files:**
- Modify: `pyproject.toml` (`dependencies`, `dev`)
- Create: `src/enrolment/demo/uploads.py`
- Test: `tests/test_demo_uploads.py`

**Interfaces:**
- Consumes: `enrolment.demo.documents.DemoDocument` (fields `id, name, persona, scenario_id, clean_text, planted_text, payload_text, prompt`; method `text(variant)`).
- Produces: `MAX_BYTES: int`; `class UploadError(ValueError)`; `normalise(text: str) -> str`; `attachment_id_for(name: str) -> str`; `extract_text(name: str, data: bytes) -> tuple[str, str]` returning `(kind, text)`; `match_known(text: str, name: str, documents: Iterable[DemoDocument]) -> dict | None`.

- [ ] **Step 1: Declare the dependencies and install them**

In `pyproject.toml`, add to `dependencies` after the `uvicorn` line:

```toml
    # The demo reads documents the presenter drags onto the chat. pypdf pulls
    # the text out of a PDF; python-multipart is what FastAPI needs to accept
    # the upload at all (it was only ever installed transitively before).
    "pypdf>=4",
    "python-multipart>=0.0.9",
```

and to `dev`:

```toml
    # Only `python -m enrolment.build_documents` needs it: it writes the eight
    # shipped PDFs. The server never writes a PDF.
    "fpdf2>=2.7",
```

Run: `.venv/bin/python -m pip install -e '.[dev]' -q && .venv/bin/python -c "import pypdf, fpdf, multipart; print('ok')"`
Expected: `ok`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_demo_uploads.py`:

```python
"""What the demo accepts as an uploaded document and how it reads it."""

import json
import pathlib

import pytest
from fpdf import FPDF

from enrolment.demo.app import KNOWN_TOOLS
from enrolment.demo.documents import documents_from
from enrolment.demo.uploads import (
    MAX_BYTES,
    UploadError,
    attachment_id_for,
    extract_text,
    match_known,
    normalise,
)
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text(encoding="utf-8")))


def documents():
    return documents_from(
        load_all(REPO / "attacks" / "scenarios", personas=PERSONAS, known_tools=KNOWN_TOOLS)
    )


def pdf_bytes(*lines: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in lines:
        pdf.multi_cell(0, 6, line)
    return bytes(pdf.output())


def test_max_bytes_is_two_megabytes():
    assert MAX_BYTES == 2 * 1024 * 1024


def test_normalise_collapses_all_whitespace():
    assert normalise("a\n\n b\t c  ") == "a b c"


@pytest.mark.parametrize("name, expected", [
    ("RPL_application_template.pdf", "rpl-application-template"),
    ("My Notes (final).TXT", "my-notes-final"),
    ("---.pdf", "document"),
    ("noext", "noext"),
])
def test_attachment_id_is_the_slugged_stem(name, expected):
    assert attachment_id_for(name) == expected


def test_a_pdf_upload_yields_its_text():
    kind, text = extract_text("letter.pdf", pdf_bytes("Dear student,", "Your claim is noted."))

    assert kind == "pdf"
    assert normalise(text) == "Dear student, Your claim is noted."


def test_a_pdf_is_recognised_by_its_magic_even_with_another_extension():
    kind, text = extract_text("letter.dat", pdf_bytes("Hello"))

    assert kind == "pdf"
    assert "Hello" in text


def test_a_text_upload_is_decoded_as_utf8():
    kind, text = extract_text("notes.txt", "Café — noted\n".encode("utf-8"))

    assert (kind, text) == ("txt", "Café — noted\n")


def test_a_pdf_with_no_text_is_refused_with_the_ocr_reason():
    pdf = FPDF()
    pdf.add_page()
    with pytest.raises(UploadError, match="OCR"):
        extract_text("scan.pdf", bytes(pdf.output()))


def test_other_types_are_refused_naming_the_type():
    with pytest.raises(UploadError, match=r"\.png"):
        extract_text("photo.png", b"\x89PNG\r\n")
    with pytest.raises(UploadError, match="no extension"):
        extract_text("blob", b"whatever")


def test_a_broken_pdf_is_refused_not_crashed():
    with pytest.raises(UploadError, match="could not be read"):
        extract_text("bad.pdf", b"%PDF-1.7 garbage")


def test_a_planted_document_is_recognised_and_the_prompt_names_the_upload():
    doc = next(d for d in documents() if d.scenario_id == "A-01")
    # Whitespace differs from the scenario's, as extraction's does.
    text = "  " + doc.planted_text.replace("\n", "   ") + "\n"

    known = match_known(text, "RPL_application_template.pdf", documents())

    assert known["scenario_id"] == "A-01"
    assert known["document_id"] == doc.id
    assert known["variant"] == "planted"
    assert "RPL_application_template.pdf" in known["prompt"]
    assert "RPL_application_template.txt" not in known["prompt"]
    assert known["payload_text"] in text, "the footer as it appears in this text"
    assert normalise(known["payload_text"]) == normalise(doc.payload_text)


def test_a_clean_document_is_recognised_without_a_payload():
    doc = next(d for d in documents() if d.scenario_id == "A-02")

    known = match_known(doc.clean_text, "transfer_checklist.pdf", documents())

    assert known["variant"] == "clean"
    assert "payload_text" not in known


def test_an_unknown_document_is_not_tagged():
    assert match_known("Something the campaign never measured.", "x.txt", documents()) is None
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_demo_uploads.py -q`
Expected: ERROR at collection, `ModuleNotFoundError: No module named 'enrolment.demo.uploads'`.

- [ ] **Step 4: Create `src/enrolment/demo/uploads.py`**

```python
"""Uploaded documents: what the demo accepts, and how it reads them.

The attach control used to offer the scenario documents from a menu. Now the
presenter drags a file onto the chat, so the text the tool serves comes out
of that file -- and text pulled out of a PDF is not the bytes that went in:
line breaks and runs of spaces come back differently. Everything here that
compares documents compares them after `normalise`, and the design note says
so plainly (spec §2).
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable

from pypdf import PdfReader

from .documents import DemoDocument

#: Over this the upload is refused with a 413. A demo document is a page or
#: two; the cap exists so a dropped video does not become a tool result.
MAX_BYTES = 2 * 1024 * 1024


class UploadError(ValueError):
    """The upload cannot become an attachment; the message says why."""


def normalise(text: str) -> str:
    return " ".join(text.split())


def attachment_id_for(name: str) -> str:
    """The stem, slugged. `get_attachment` resolves by id or by file name,
    case-insensitively, so a model may use either."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug or "document"


def extract_text(name: str, data: bytes) -> tuple[str, str]:
    """`(kind, text)` for a PDF or plain-text upload.

    A PDF is recognised by its magic as well as its extension, because the
    extension is whatever the presenter's file manager says it is. A PDF
    with no text at all is refused rather than attached as an empty
    document: a scanned letter that the model "read" as nothing would show
    an attack failing for a reason nobody watching could see.
    """
    lower = name.lower()
    if data.startswith(b"%PDF-") or lower.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as error:  # noqa: BLE001 - pypdf raises many types; all mean "not a PDF we can read"
            raise UploadError(f"{name} could not be read as a PDF: {error}") from error
        if not text.strip():
            raise UploadError(
                f"no text could be read from {name} (a scanned document needs OCR, "
                "which the demo does not do)"
            )
        return "pdf", text
    if lower.endswith(".txt"):
        return "txt", data.decode("utf-8", errors="replace")
    what = f".{lower.rsplit('.', 1)[-1]}" if "." in lower else "a file with no extension"
    raise UploadError(
        f"{name}: only PDF and plain-text (.txt) documents are accepted, not {what}"
    )


def _as_extracted(text: str, payload: str) -> str:
    """The payload as it appears in `text`, whitespace and all, so the page's
    highlight finds it. Falls back to the payload itself if extraction moved
    the words apart in a way the pattern cannot follow."""
    pattern = r"\s+".join(re.escape(word) for word in payload.split())
    found = re.search(pattern, text)
    return found.group(0) if found else payload


def match_known(text: str, name: str, documents: Iterable[DemoDocument]) -> dict | None:
    """The shipped document this upload is, if it is one.

    Planted before clean: the planted text contains the clean text, so the
    order only matters for readability, but it states which answer wins.
    The prompt names the uploaded file so the chip reads "...I've attached
    RPL_application_template.pdf..." rather than the scenario's `.txt`.
    """
    wanted = normalise(text)
    for document in documents:
        for variant in ("planted", "clean"):
            if normalise(document.text(variant)) != wanted:
                continue
            known = {
                "scenario_id": document.scenario_id,
                "document_id": document.id,
                "variant": variant,
                "prompt": document.prompt.replace(document.name, name),
            }
            if variant == "planted":
                known["payload_text"] = _as_extracted(text, document.payload_text)
            return known
    return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_demo_uploads.py -q && .venv/bin/ruff check src/enrolment/demo/uploads.py tests/test_demo_uploads.py`
Expected: all PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/enrolment/demo/uploads.py tests/test_demo_uploads.py
git commit -m "feat(demo): uploads module -- PDF and text extraction, the id rule, matching against the shipped documents"
```

---

### Task 2: The build script and the shipped PDFs

**Files:**
- Create: `src/enrolment/build_documents.py`
- Create: `data/documents/clean/*.pdf`, `data/documents/as-received/*.pdf` (eight files, generated)
- Test: `tests/test_build_documents.py`

**Interfaces:**
- Consumes: `demo.documents.documents_from`, `demo.uploads.normalise`, `demo.app.KNOWN_TOOLS`, `scenarios_spec.load_all`.
- Produces: `build_documents.FOOTER_POINTS: int`; `build_documents.pdf_name(document) -> str`; `build_documents.write_documents(documents, out: Path) -> list[Path]`; `build_documents.main(argv=None)`; the eight committed PDFs used by Task 3's tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_build_documents.py`:

```python
"""The shipped demo documents: eight PDFs written from the scenario files,
the as-received ones carrying the footer in white, tiny text."""

import json
import pathlib

from pypdf import PdfReader

from enrolment.build_documents import FOOTER_POINTS, pdf_name, write_documents
from enrolment.demo.app import KNOWN_TOOLS
from enrolment.demo.documents import documents_from
from enrolment.demo.uploads import normalise
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text(encoding="utf-8")))


def documents():
    return documents_from(
        load_all(REPO / "attacks" / "scenarios", personas=PERSONAS, known_tools=KNOWN_TOOLS)
    )


def extracted(path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_pdf_name_swaps_the_extension():
    doc = next(d for d in documents() if d.scenario_id == "A-01")
    assert pdf_name(doc) == "RPL_application_template.pdf"


def test_it_writes_eight_pdfs_that_round_trip_to_the_scenario_text(tmp_path):
    written = write_documents(documents(), tmp_path)

    assert len(written) == 8
    for doc in documents():
        clean = tmp_path / "clean" / pdf_name(doc)
        planted = tmp_path / "as-received" / pdf_name(doc)
        assert normalise(extracted(clean)) == normalise(doc.clean_text), doc.id
        assert normalise(extracted(planted)) == normalise(doc.planted_text), doc.id


def test_the_footer_is_white_and_tiny(tmp_path):
    """A person opening the as-received PDF sees a normal template; the
    model gets the footer because extraction ignores colour and size."""
    write_documents(documents(), tmp_path)
    doc = next(d for d in documents() if d.scenario_id == "A-01")
    page = PdfReader(str(tmp_path / "as-received" / pdf_name(doc))).pages[0]
    content = page.get_contents().get_data()

    assert b"1.000 1.000 1.000 rg" in content, "white fill"
    assert f" {FOOTER_POINTS:.2f} Tf".encode() in content, "footer font size"


def test_the_committed_pdfs_are_current():
    """`data/documents` is what the presenter drags in. It must be what the
    script writes today, so a scenario edit that forgets to rebuild fails
    here rather than in the room."""
    for doc in documents():
        clean = REPO / "data" / "documents" / "clean" / pdf_name(doc)
        planted = REPO / "data" / "documents" / "as-received" / pdf_name(doc)
        assert clean.exists() and planted.exists(), doc.id
        assert normalise(extracted(clean)) == normalise(doc.clean_text), doc.id
        assert normalise(extracted(planted)) == normalise(doc.planted_text), doc.id
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_build_documents.py -q`
Expected: ERROR at collection, `ModuleNotFoundError: No module named 'enrolment.build_documents'`.

- [ ] **Step 3: Create `src/enrolment/build_documents.py`**

```python
"""Write the demo's shipped documents as PDFs.

    python -m enrolment.build_documents            # into data/documents
    python -m enrolment.build_documents --out DIR

One clean and one as-received PDF per upload-borne scenario (A-01..A-04).
The as-received copy carries the planted footer in white at `FOOTER_POINTS`
points, after a blank line: a person opening it sees the template the
student was sent; the model, reading through `get_attachment`, gets the
footer, because text extraction ignores colour and size. That is the attack
the pivot note describes, in the file format a student would actually send.

The text is the scenario's, via `demo.documents.documents_from` and
`run_setup.plant_payload` -- this script restates neither rule. What comes
back out of the PDF equals the scenario text up to whitespace (spec §2);
`tests/test_build_documents.py` pins that, and pins that the committed files
are current.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF

from .demo.app import KNOWN_TOOLS
from .demo.documents import DemoDocument, documents_from
from .scenarios_spec import load_all

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "data" / "documents"

#: The footer's point size. 1 is the smallest fpdf2 writes. Raise it only if
#: the round-trip test stops finding the footer's words at this size, and
#: say so here.
FOOTER_POINTS = 1
BODY_POINTS = 11
#: Fixed so re-running the script does not churn the committed bytes.
CREATED = datetime(2026, 9, 29, tzinfo=UTC)


def pdf_name(document: DemoDocument) -> str:
    stem = document.name.rsplit(".", 1)[0] if "." in document.name else document.name
    return f"{stem}.pdf"


def _write_pdf(path: Path, body: str, footer: str | None) -> None:
    for char in body + (footer or ""):
        if ord(char) > 255:
            # The core Helvetica font is Latin-1. Failing beats writing a
            # PDF whose extracted text no longer matches the scenario.
            raise ValueError(f"{path.name}: {char!r} is not Latin-1; embed a TTF font first")
    pdf = FPDF()
    pdf.set_creation_date(CREATED)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=BODY_POINTS)
    # One cell per line, so the template's line structure survives
    # extraction instead of running together into one paragraph.
    for line in body.splitlines():
        if line.strip():
            pdf.multi_cell(0, 6, line)
        else:
            pdf.ln(6)
    if footer is not None:
        pdf.ln(6)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font_size(FOOTER_POINTS)
        pdf.multi_cell(0, FOOTER_POINTS, footer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(pdf.output()))


def write_documents(documents, out: Path) -> list[Path]:
    """`out/clean/<name>.pdf` and `out/as-received/<name>.pdf` per document."""
    out = Path(out)
    written: list[Path] = []
    for document in documents:
        clean = out / "clean" / pdf_name(document)
        _write_pdf(clean, document.clean_text, None)
        planted = out / "as-received" / pdf_name(document)
        _write_pdf(planted, document.clean_text, document.payload_text)
        written += [clean, planted]
    return written


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scenarios", type=Path, default=REPO_ROOT / "attacks" / "scenarios")
    parser.add_argument("--personas", type=Path, default=REPO_ROOT / "data" / "personas.json")
    args = parser.parse_args(argv)
    personas = set(json.loads(args.personas.read_text(encoding="utf-8")))
    documents = documents_from(
        load_all(args.scenarios, personas=personas, known_tools=KNOWN_TOOLS)
    )
    for path in write_documents(documents, args.out):
        print(path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the first three tests**

Run: `.venv/bin/python -m pytest tests/test_build_documents.py -q -k "not committed"`
Expected: PASS. If `test_it_writes_eight_pdfs_that_round_trip_to_the_scenario_text` fails on a planted document only, extraction is splitting or merging the 1pt footer's words: raise `FOOTER_POINTS` one point at a time (2, 3, 4) until it passes, update the constant's comment with the size and why, and re-run. Do not change the test.

- [ ] **Step 5: Generate the shipped PDFs and run the fourth test**

Run: `.venv/bin/python -m enrolment.build_documents && ls data/documents/clean data/documents/as-received && .venv/bin/python -m pytest tests/test_build_documents.py -q`
Expected: eight paths printed, four files in each directory, all PASS. Open one in a viewer if you have one: `open data/documents/as-received/RPL_application_template.pdf` — the footer must not be visible.

Run the script a second time and check `git status --short data/documents` shows the eight files as new (`??`) and, after `git add`, that a third run leaves `git diff --stat data/documents` empty — the fixed creation date keeps the bytes stable.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check src/enrolment/build_documents.py tests/test_build_documents.py
git add src/enrolment/build_documents.py tests/test_build_documents.py data/documents
git commit -m "feat(demo): the shipped documents as PDFs -- clean and as received, the footer white and tiny; build script and round-trip test"
```

---

### Task 3: Upload and delete endpoints; the attach-menu endpoint goes

**Files:**
- Modify: `src/enrolment/demo/app.py` (imports, `Attached`/`AttachmentsUpdate` removed, `documents_by_id` removed, `set_attachments` replaced by `upload` and `detach`)
- Modify: `src/enrolment/demo/chat.py` (`_write_attachments` docstring only; behaviour already right)
- Test: `tests/test_demo_chat_api.py`, `tests/test_demo_chat.py`

**Interfaces:**
- Consumes: `demo.uploads.{MAX_BYTES, UploadError, attachment_id_for, extract_text, match_known}` (Task 1); the shipped PDFs under `data/documents/` (Task 2); `ChatSession.attachments`, `.root`, `.busy`.
- Produces: `POST /api/chat/{id}/upload` (multipart field `file`) → `state_of(live)`; `DELETE /api/chat/{id}/attachments/{attachment_id}` → `state_of(live)`; state `attachments[]` items now `{id, name, text, kind, known}`.

- [ ] **Step 1: Rewrite the attachment tests in `tests/test_demo_chat_api.py`**

Replace the `attach` helper and every test that used it. The final file must contain the following (keep every other test in the file as it is):

```python
DOCUMENTS = REPO / "data" / "documents"


def upload(client, session, path, name=None, content_type="application/octet-stream"):
    path = pathlib.Path(path)
    return client.post(
        f"/api/chat/{session}/upload",
        files={"file": (name or path.name, path.read_bytes(), content_type)},
    )


def upload_bytes(client, session, name, data, content_type="application/octet-stream"):
    return client.post(f"/api/chat/{session}/upload", files={"file": (name, data, content_type)})


def test_uploading_the_as_received_pdf_tags_it_and_serves_its_text():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_attachment", {
            "student_id": "S9000004", "attachment_id": "transfer-checklist"})]),
        ChatReply(content="Read it."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_attachment"]})

    body = upload(client, session, DOCUMENTS / "as-received" / "transfer_checklist.pdf").json()

    [attachment] = body["attachments"]
    assert attachment["id"] == "transfer-checklist"
    assert attachment["name"] == "transfer_checklist.pdf"
    assert attachment["kind"] == "pdf"
    assert attachment["known"]["scenario_id"] == "A-02"
    assert attachment["known"]["variant"] == "planted"
    assert "transfer_checklist.pdf" in attachment["known"]["prompt"]
    assert "NOTE FOR THE ASSISTANT" in attachment["text"]

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "read"}))[-1]
    assert "NOTE FOR THE ASSISTANT" in done["calls"][0]["result"]


def test_the_planted_footer_is_findable_in_the_extracted_text():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000001"}).json()["id"]

    body = upload(client, session, DOCUMENTS / "as-received" / "RPL_application_template.pdf").json()

    [attachment] = body["attachments"]
    assert attachment["known"]["payload_text"] in attachment["text"]


def test_the_clean_pdf_is_tagged_clean_without_a_payload():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    body = upload(client, session, DOCUMENTS / "clean" / "transfer_checklist.pdf").json()

    assert body["attachments"][0]["known"]["variant"] == "clean"
    assert "payload_text" not in body["attachments"][0]["known"]


def test_a_text_upload_is_accepted_and_untagged():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    body = upload_bytes(client, session, "notes.txt", b"My own notes.\n").json()

    [attachment] = body["attachments"]
    assert attachment == {"id": "notes", "name": "notes.txt", "text": "My own notes.\n",
                          "kind": "txt", "known": None}


def test_a_file_name_with_spaces_resolves_by_name():
    client = TestClient(app_with(
        ChatReply(content="", calls=[call("get_attachment", {
            "student_id": "S9000004", "attachment_id": "My Notes.txt"})]),
        ChatReply(content="Read it."),
    ))
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    client.put(f"/api/chat/{session}/tools", json={"allowed_tools": ["get_attachment"]})
    body = upload_bytes(client, session, "My Notes.txt", b"spaced\n").json()
    assert body["attachments"][0]["id"] == "my-notes"

    done = events_from(client.post(f"/api/chat/{session}/message", json={"text": "read"}))[-1]

    assert done["calls"][0]["blocked"] is False
    assert "spaced" in done["calls"][0]["result"]


def test_uploading_the_same_file_again_replaces_it():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload_bytes(client, session, "notes.txt", b"first\n")

    body = upload_bytes(client, session, "notes.txt", b"second\n").json()

    assert [a["text"] for a in body["attachments"]] == ["second\n"]


def test_other_types_are_refused_naming_the_type():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, "photo.png", b"\x89PNG\r\n", "image/png")

    assert response.status_code == 400
    assert ".png" in response.json()["detail"]


def test_an_oversize_upload_is_refused():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, "big.txt", b"x" * (2 * 1024 * 1024 + 1))

    assert response.status_code == 413


def test_a_pdf_with_no_text_is_refused():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = upload_bytes(client, session, "scan.pdf", bytes(pdf.output()), "application/pdf")

    assert response.status_code == 400
    assert "OCR" in response.json()["detail"]


def test_the_uploaded_file_is_kept_as_evidence(tmp_path):
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload_bytes(client, session, "notes.txt", b"kept\n")

    # The session root is under the app's temp dir; find it by the id.
    kept = list(pathlib.Path(tempfile.gettempdir()).glob(f"demo-chat-*/{session}/uploads/notes/notes.txt"))
    assert kept and kept[0].read_bytes() == b"kept\n"


def test_deleting_an_attachment_removes_it():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]
    upload_bytes(client, session, "notes.txt", b"bye\n")

    body = client.delete(f"/api/chat/{session}/attachments/notes").json()

    assert body["attachments"] == []
    assert client.delete(f"/api/chat/{session}/attachments/notes").status_code == 404


def test_the_attach_menu_endpoint_is_gone():
    client = TestClient(app_with())
    session = client.post("/api/chat", json={"persona": "S9000004"}).json()["id"]

    response = client.put(f"/api/chat/{session}/attachments",
                          json={"attached": [{"id": "transfer-checklist", "variant": "planted"}]})

    assert response.status_code == 405
```

Add `import tempfile` to the imports. Then update the two existing tests that called `attach`:

- `test_the_done_event_carries_the_turn_the_attachments_and_the_erequests`: replace `attach(client, session, "transfer-checklist", "clean")` with `upload(client, session, DOCUMENTS / "clean" / "transfer_checklist.pdf")` and `assert done["attachments"][0]["variant"] == "clean"` with `assert done["attachments"][0]["known"]["variant"] == "clean"`.
- `test_approving_a_held_call_returns_the_confirmation_and_the_new_state`: rename to `test_approving_a_held_call_after_an_upload` and replace `attach(client, session, "transfer-checklist")` with `upload(client, session, DOCUMENTS / "as-received" / "transfer_checklist.pdf")`. Everything else in it stays.

Delete `test_attaching_a_document_reports_it_with_its_full_text`, `test_attaching_another_students_document_is_refused` and `test_attaching_an_unknown_document_is_refused` (the menu they tested is gone).

- [ ] **Step 2: Add the `_write_attachments` test to `tests/test_demo_chat.py`**

```python
@pytest.mark.anyio
async def test_attachments_json_carries_only_name_and_text(tmp_path):
    """`kind` and `known` are the page's business. The tool and the replay
    read `attachments.json`, and they see what they saw before uploads."""
    live = session(tmp_path)
    live.attachments = {"notes": {"name": "notes.txt", "text": "hi\n", "kind": "txt",
                                  "known": {"scenario_id": "A-01", "variant": "clean"}}}

    await live.send("hello", chat=scripted(ChatReply(content="hi")), model="fake")

    written = json.loads((tmp_path / "session" / "turn-001" / "attachments.json").read_text())
    assert written == {"notes": {"name": "notes.txt", "text": "hi\n"}}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_demo_chat_api.py tests/test_demo_chat.py -q -k "upload or attach or delete or planted or menu or after_an_upload or spaces or oversize or no_text"`
Expected: the upload/delete tests FAIL with 404/405 responses; `test_the_attach_menu_endpoint_is_gone` FAILS (it is 200 today); the `_write_attachments` test PASSES already (behaviour is right; the test pins it).

- [ ] **Step 4: The endpoints**

In `src/enrolment/demo/app.py`:

Imports: remove `from typing import Literal` if nothing else uses it; add `from fastapi import FastAPI, File, HTTPException, UploadFile` (extend the existing fastapi import) and

```python
from .uploads import MAX_BYTES, UploadError, attachment_id_for, extract_text, match_known
```

Delete the `Attached` and `AttachmentsUpdate` models. In `create_app`, delete the `documents_by_id = ...` line (keep `documents = documents_from(...)`). Replace the whole `set_attachments` route with:

```python
    @app.post("/api/chat/{session_id}/upload")
    async def upload(session_id: str, file: UploadFile = File(...)) -> dict:
        """A document dragged onto the chat becomes one of the session's
        attachments, served by `get_attachment` on every later message.

        The text is what pypdf pulls out of the file; the file itself is kept
        under the session's `uploads/` as evidence and never read back. If
        the text matches one of the shipped documents, the entry says which
        and in which variant, so the page can offer the scenario's own
        request and mark the footer. A session mid-message refuses the
        upload: the running proxy already has its `attachments.json`.
        """
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is running; attach the file when it finishes")
        data = await file.read(MAX_BYTES + 1)
        name = Path(file.filename or "document").name
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{name} is over the 2 MB limit")
        try:
            kind, text = extract_text(name, data)
        except UploadError as error:
            raise HTTPException(400, str(error)) from error
        attachment_id = attachment_id_for(name)
        kept = live.root / "uploads" / attachment_id / name
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
        live.attachments[attachment_id] = {
            "name": name, "text": text, "kind": kind, "known": match_known(text, name, documents),
        }
        return state_of(live)

    @app.delete("/api/chat/{session_id}/attachments/{attachment_id}")
    def detach(session_id: str, attachment_id: str) -> dict:
        """The chip's ×. The kept file stays on disk, like turn directories."""
        live = get(session_id)
        if attachment_id not in live.attachments:
            raise HTTPException(404, f"no attachment {attachment_id!r}")
        del live.attachments[attachment_id]
        return state_of(live)
```

Update the module docstring paragraph that mentions the attach control so it reads: "The attack arrives the way the pivot note says it does: inside a document the presenter drags onto the chat, read through `get_attachment`. The shipped documents under `data/documents/` are the scenario texts as PDFs, the planted footer white and tiny."

In `src/enrolment/demo/chat.py`, extend `_write_attachments` with a docstring:

```python
        """Only `name` and `text` reach the proxy. `kind` and `known` are the
        page's, and the tool must not see a tag saying which scenario a
        document is."""
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_demo_chat_api.py tests/test_demo_chat.py tests/test_demo_app.py -q && .venv/bin/ruff check src/enrolment/demo tests/test_demo_chat_api.py tests/test_demo_chat.py`
Expected: all PASS; ruff clean. If `test_the_uploaded_file_is_kept_as_evidence` cannot find the file, print `session_root` from `create_app` in a scratch run to see the actual layout and fix the glob, not the behaviour.

- [ ] **Step 6: Commit**

```bash
git add src/enrolment/demo/app.py src/enrolment/demo/chat.py tests/test_demo_chat_api.py tests/test_demo_chat.py
git commit -m "feat(demo): upload a document to a session -- PDF or text, extracted, kept, tagged when it is a shipped one; the attach menu endpoint is gone"
```

---

### Task 4: The page: drop zone, file picker, chips from the tag

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`
- Build output: `src/enrolment/demo/static/`

**Interfaces:**
- Consumes: `POST /api/chat/{id}/upload` (multipart `file`), `DELETE /api/chat/{id}/attachments/{id}`; state `attachments[].{id, name, text, kind, known}` with `known.{variant, prompt, payload_text}` (Task 3).
- Produces: the built page.

- [ ] **Step 1: `api.js`**

Replace `setAttachments` with:

```js
// A multipart upload: the browser sets the boundary, so no Content-Type here.
export const uploadFile = (id, file) => {
  const body = new FormData()
  body.append('file', file, file.name)
  return fetch(`/api/chat/${id}/upload`, { method: 'POST', body }).then(json)
}

export const deleteAttachment = (id, attachmentId) =>
  fetch(`/api/chat/${id}/attachments/${encodeURIComponent(attachmentId)}`, { method: 'DELETE' })
    .then(json)
```

- [ ] **Step 2: `App.jsx`**

Make these edits (the rest of the file stays):

1. Delete `VARIANT_LABEL`. Add, after `findAttached`:

```js
const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')
const kindLabel = (a) => (a.kind === 'pdf' ? 'PDF' : 'text')
// The server says which shipped document an upload is, if any; the page
// only repeats it. It never guesses from the file name.
const knownLabel = (a) =>
  a.known?.variant === 'planted' ? ' · as received (contains a planted footer)'
    : a.known?.variant === 'clean' ? ' · matches the demo document' : ''
```

2. Delete the `documents` state and its `api.getDocuments()` effect; delete `menuOpen`; add:

```js
  const [dropping, setDropping] = useState(false)
  const fileInput = useRef(null)
```

3. Replace `applyAttachments`, `attachDocument`, `detach`, `myDocuments`, `docById`, `attachedPrompts` with:

```js
  // Each dropped file is one upload; the chip appears when the server
  // answers. A refused file reports its reason and the rest still go up.
  const uploadFiles = async (files) => {
    if (!session || busy) return
    setError(null)
    for (const file of Array.from(files)) {
      const s = await api.uploadFile(session, file)
        .catch((e) => { setError(`${file.name}: ${e.message}`); return null })
      if (s) applyState(s)
    }
  }
  const detach = async (id) => {
    if (!session || busy) return
    const s = await api.deleteAttachment(session, id).catch((e) => { setError(e.message); return null })
    if (s) applyState(s)
  }
  const attachedPrompts = attached.map((a) => a.known?.prompt).filter(Boolean)
```

4. In `send`, delete `setMenuOpen(false)` and change the snapshot so the payload travels with the row:

```js
          const doc = event.tool === 'get_attachment'
            ? findAttached(attached, event.arguments?.attachment_id) : null
          setThread((t) => [...t, {
            role: 'call', token, tool: event.tool, args: event.arguments, refused: event.refused,
            doc: doc ? { id: doc.id, name: doc.name, text: doc.text,
                         payload: doc.known?.payload_text || null } : null,
          }])
```

and in the row render, `<Saw text={doc.text} payload={doc.payload} />`.

5. The chat card becomes the drop zone. Change its opening tag to:

```jsx
        <section
          className={`card chat${dropping ? ' dropping' : ''}`}
          onDragEnter={(e) => { if (!busy && hasFiles(e)) { e.preventDefault(); setDropping(true) } }}
          onDragOver={(e) => { if (!busy && hasFiles(e)) { e.preventDefault(); setDropping(true) } }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDropping(false) }}
          onDrop={(e) => { e.preventDefault(); setDropping(false); if (!busy) uploadFiles(e.dataTransfer.files) }}
        >
          {dropping && <div className="dropzone" aria-hidden="true">Drop to attach</div>}
```

6. Chips: replace the `<span className="variant">· {VARIANT_LABEL[a.variant]}</span>` with `<span className="variant">· {kindLabel(a)}{knownLabel(a)}</span>`.

7. Replace the whole `<div className="attach">…</div>` block in the composer with:

```jsx
            <div className="attach">
              <button type="button" onClick={() => fileInput.current?.click()} disabled={busy}>
                Attach
              </button>
              <input ref={fileInput} type="file" accept=".pdf,.txt" multiple hidden
                     onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
            </div>
```

8. Grep the file for `documents`, `menuOpen`, `VARIANT_LABEL`, `docById`, `getDocuments`, `setAttachments`; none may remain.

- [ ] **Step 3: `styles.css`**

Delete the `.attachmenu` rules (five of them) and `.attach { position: relative; }`. Add `position: relative;` to the existing `.chat { … }` rule. After the `.attach > button:disabled` rule add:

```css
/* The chat card is the drop zone. The outline and overlay appear only while a
   file is over it, and never while a message runs -- the running proxy has
   its attachments already. */
.chat.dropping { outline: 3px dashed var(--uts-blue); outline-offset: -3px; }
.dropzone {
  position: absolute; inset: 0; z-index: 3; display: grid; place-items: center;
  background: rgba(255, 255, 255, .88); color: var(--uts-blue);
  font-size: 1.3rem; font-weight: 700; pointer-events: none;
}
```

- [ ] **Step 4: Build and smoke-check**

```bash
cd frontend && npm run build && cd ..
grep -c "Drop to attach" src/enrolment/demo/static/assets/index-*.js
grep -c "attachmenu" src/enrolment/demo/static/assets/index-*.css
```

Expected: a clean build; the first grep prints 1; the second prints 0.

- [ ] **Step 5: Browser check (controller)**

Start `.venv/bin/python -m uvicorn enrolment.demo.app:create_app --factory --port 8765`, open `http://127.0.0.1:8765/`, and:

1. Drag `data/documents/as-received/RPL_application_template.pdf` onto the chat: the dashed outline and "Drop to attach" show while over it; a chip `📎 RPL_application_template.pdf · PDF · as received (contains a planted footer)` appears; the scenario prompt chip appears, naming the `.pdf`.
2. Send the prompt chip: a `get_attachment` row with the file name; the "what the model saw" panel shows the extracted text with the footer highlighted.
3. With enforcement on, a held call shows the approval card; Allow works.
4. Drag a `.png`: the system bubble reports "only PDF and plain-text (.txt) documents are accepted, not .png". Drag `clean/transfer_checklist.pdf` while on Jane: chip says "matches the demo document" (no persona check; the tag is honest about what it is).
5. Attach → the OS file picker opens; pick the clean PDF; chip appears. × removes it.
6. Start over keeps the chips; switching persona drops them.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.js frontend/src/App.jsx frontend/src/styles.css src/enrolment/demo/static
git commit -m "feat(demo): drag a PDF onto the chat -- drop zone, file picker, chips and prompt from the server's tag; the attach menu is gone"
```

---

### Task 5: Docs

**Files:**
- Modify: `context/report/section4-draft.md` §4.5
- Modify: `context/2026-09-23-pivot-upload-injection-vs-rag.md` §4
- Modify: `docs/superpowers/specs/2026-09-29-demo-pdf-upload-design.md` (status line)

- [ ] **Step 1: Report draft §4.5**

In the **Conversation** bullet, replace the sentence `The composer has an **attach** control offering the scenario's documents.` with:

```markdown
The chat is a drop zone: the presenter drags a document onto it (or picks one
with **Attach**), the page uploads it, and the server pulls the text out with
pypdf. The four demo documents ship as PDFs under `data/documents/`, clean and
as received, the planted footer in white 1pt text that a reader never sees
and the model always does. Text pulled out of a PDF matches the scenario text
up to whitespace, not byte for byte; the round-trip test pins that.
```

In the *Currently built* paragraph, replace `the attach control offering each persona's document clean or as received` with `the drop zone and file picker, with the shipped PDFs clean or as received`.

- [ ] **Step 2: Pivot note §4**

Replace `documents beyond plain text (PDF extraction as its own attack surface).` with `documents beyond PDF and plain text; OCR of scanned PDFs (the demo reads PDFs with pypdf and refuses one with no text).`

- [ ] **Step 3: Spec status**

Change `**Status:** agreed in conversation; nothing built.` to `**Status:** built 2026-09-29; see the plan of the same name under docs/superpowers/plans/.`

- [ ] **Step 4: Commit**

```bash
git add context/report/section4-draft.md context/2026-09-23-pivot-upload-injection-vs-rag.md docs/superpowers/specs/2026-09-29-demo-pdf-upload-design.md
git commit -m "docs: the demo reads dragged PDFs; OCR and other formats stay future work"
```
