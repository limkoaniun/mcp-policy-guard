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

#: Over this many characters the file name is refused with a 400, before it
#: touches disk. Most filesystems cap a single path component at 255 bytes;
#: both the attachment id and the kept-evidence file are derived from this
#: name, and the name of a dropped file is attacker-controlled, so an
#: oversize one must be a clean refusal rather than an `OSError` from
#: `mkdir`/`write_bytes` down in the route.
MAX_NAME = 200


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
        except Exception as error:
            # pypdf raises many types; all mean "not a PDF we can read"
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
