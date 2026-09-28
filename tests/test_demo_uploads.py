"""What the demo accepts as an uploaded document and how it reads it."""

import json
import pathlib

import pytest
from fpdf import FPDF

from enrolment.demo.app import KNOWN_TOOLS
from enrolment.demo.documents import documents_from
from enrolment.demo.uploads import (
    MAX_BYTES,
    MAX_NAME,
    UploadError,
    attachment_id_for,
    extract_text,
    match_known,
    normalise,
    safe_name,
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
        pdf.cell(0, 6, line)
        pdf.ln(6)
    return bytes(pdf.output())


def test_max_bytes_is_two_megabytes():
    assert MAX_BYTES == 2 * 1024 * 1024


def test_safe_name_passes_through_a_normal_name():
    assert safe_name("RPL_application_template.pdf") == "RPL_application_template.pdf"


def test_safe_name_strips_directory_components():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("a/b/notes.txt") == "notes.txt"


@pytest.mark.parametrize("name", ["..", ".", "", None])
def test_safe_name_refuses_a_dot_or_empty_name(name):
    with pytest.raises(UploadError, match="no usable file name"):
        safe_name(name)


def test_safe_name_refuses_a_name_over_the_limit():
    with pytest.raises(UploadError, match="too long"):
        safe_name(("x" * 300) + ".txt")
    assert safe_name(("y" * (MAX_NAME - 4)) + ".txt")


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
    kind, text = extract_text("notes.txt", "Café — noted\n".encode())

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
