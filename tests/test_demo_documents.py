"""The documents the demo's attach control offers: one per upload persona,
built from the scenario files so the demo attaches the bytes the campaign
measured."""

import json
import pathlib

from enrolment.demo.app import KNOWN_TOOLS
from enrolment.demo.documents import documents_from
from enrolment.run_setup import plant_payload
from enrolment.scenarios_spec import load_all

REPO = pathlib.Path(__file__).resolve().parents[1]
PERSONAS = set(json.loads((REPO / "data" / "personas.json").read_text(encoding="utf-8")))


def scenarios():
    return load_all(REPO / "attacks" / "scenarios", personas=PERSONAS, known_tools=KNOWN_TOOLS)


def by_id():
    return {s.id: s for s in scenarios()}


def test_one_document_per_upload_persona():
    docs = documents_from(scenarios())

    assert sorted(d.persona for d in docs) == ["S9000001", "S9000002", "S9000003", "S9000004"]
    assert sorted(d.scenario_id for d in docs) == ["A-01", "A-02", "A-03", "A-04"]


def test_the_planted_text_is_the_campaigns_and_the_clean_text_is_the_twins():
    doc = next(d for d in documents_from(scenarios()) if d.scenario_id == "A-02")
    twin = by_id()["A-02b"]

    assert doc.id == "transfer-checklist"
    assert doc.name == "transfer_checklist.txt"
    assert doc.clean_text == twin.attachments[0].text
    assert doc.planted_text == plant_payload(doc.clean_text, by_id()["A-02"].payload.text)
    assert doc.planted_text.endswith(doc.payload_text)
    assert doc.prompt == by_id()["A-02"].task.description


def test_text_picks_the_variant_and_refuses_an_unknown_one():
    import pytest

    doc = documents_from(scenarios())[0]

    assert doc.text("clean") == doc.clean_text
    assert doc.text("planted") == doc.planted_text
    with pytest.raises(ValueError):
        doc.text("original")


def test_scenarios_without_an_attachment_payload_produce_no_document():
    docs = documents_from([by_id()["SC-02"], by_id()["A-01b"]])

    assert docs == []
