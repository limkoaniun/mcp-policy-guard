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
from fpdf.enums import XPos, YPos

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
    # extraction instead of running together into one paragraph. A
    # full-width (w=0) multi_cell leaves x at the right margin unless told
    # otherwise -- fpdf2 2.8's default new_x is XPos.RIGHT, not the left
    # margin -- so the next cell would open with zero width and raise
    # "Not enough horizontal space to render a single character". Send the
    # cursor back to the left margin after every cell instead.
    for line in body.splitlines():
        if line.strip():
            pdf.multi_cell(0, 6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        else:
            pdf.ln(6)
    if footer is not None:
        pdf.ln(6)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font_size(FOOTER_POINTS)
        pdf.multi_cell(0, FOOTER_POINTS, footer, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
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
