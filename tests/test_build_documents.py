"""The shipped demo documents: eight PDFs written from the scenario files,
the as-received ones carrying the footer in white, tiny text."""

import json
import pathlib

from pypdf import PdfReader

from enrolment.build_documents import BODY_POINTS, FOOTER_POINTS, pdf_name, write_documents
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

    # fpdf2 2.8 writes full-white fill as bare integers, not "1.000 1.000
    # 1.000 rg" -- confirmed against this build's actual content stream.
    # Black (or any visible colour) would not produce "1 1 1 rg", so this
    # still pins white specifically, not merely "some colour was set".
    assert b"1 1 1 rg" in content, "white fill"
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


DEMO = REPO / "attacks" / "scenarios-demo"
DEMO_OUT = REPO / "data" / "documents" / "demo"


def demo_documents():
    return documents_from(load_all(DEMO, personas=PERSONAS, known_tools=KNOWN_TOOLS))


def test_the_demo_pdfs_round_trip_to_their_scenario_text(tmp_path):
    written = write_documents(demo_documents(), tmp_path)
    assert len(written) == 6
    for document in demo_documents():
        clean = extracted(tmp_path / "clean" / pdf_name(document))
        planted = extracted(tmp_path / "as-received" / pdf_name(document))
        assert normalise(clean) == normalise(document.clean_text)
        assert normalise(planted) == normalise(document.planted_text)


def test_the_committed_demo_pdfs_are_current(tmp_path):
    write_documents(demo_documents(), tmp_path)
    for document in demo_documents():
        for variant in ("clean", "as-received"):
            name = pdf_name(document)
            assert (DEMO_OUT / variant / name).read_bytes() == (tmp_path / variant / name).read_bytes(), name


def test_the_title_line_is_set_larger_and_bold(tmp_path):
    """Realism without changing the text: the first line is the title. pypdf
    reports the font name per text run, so the title's run is the bold face."""
    [document] = [d for d in demo_documents() if d.id == "medical-certificate"]
    write_documents([document], tmp_path)
    page = PdfReader(str(tmp_path / "clean" / pdf_name(document))).pages[0]
    fonts = set()
    page.extract_text(visitor_text=lambda text, cm, tm, font, size: fonts.add(
        (font["/BaseFont"] if font and "/BaseFont" in font else None, round(size, 1))
        if text.strip() else None))
    bold_sizes = {size for base, size in fonts - {None} if base and "Bold" in str(base)}
    assert bold_sizes, fonts
    assert max(bold_sizes) > BODY_POINTS
