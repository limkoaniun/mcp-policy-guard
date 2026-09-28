"""The documents the demo's attach control offers.

One per upload persona, taken from the scenario that plants a payload in a
document (`payload.location: attachment`): A-01 Jane's RPL template, A-02
Tom's transfer checklist, A-03 Omar's group agreement, A-04 Priya's medical
letter. Both variants are offered -- the clean text, byte-identical to the
benign twin's, and the planted text built by `run_setup.plant_payload` --
so what the presenter attaches is what the campaign measured, not a document
written for the demo.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from ..run_setup import plant_payload
from ..scenarios_spec import Scenario

VARIANTS = ("clean", "planted")


class DemoDocument(BaseModel):
    id: str
    name: str
    persona: str
    scenario_id: str
    clean_text: str
    planted_text: str
    #: Kept separately so the page can highlight the footer in the planted
    #: text without re-deriving where it starts.
    payload_text: str
    #: The scenario's own request, offered as a suggestion chip while the
    #: document is attached.
    prompt: str

    def text(self, variant: str) -> str:
        if variant == "clean":
            return self.clean_text
        if variant == "planted":
            return self.planted_text
        raise ValueError(f"unknown variant {variant!r}; expected one of {list(VARIANTS)}")


def documents_from(scenarios: Iterable[Scenario]) -> list[DemoDocument]:
    """One document per scenario that plants its payload in an attachment."""
    documents = []
    for scenario in sorted(scenarios, key=lambda s: s.id):
        payload = scenario.payload
        if payload is None or payload.location != "attachment":
            continue
        attachment = next(a for a in scenario.attachments if a.id == payload.target)
        documents.append(DemoDocument(
            id=attachment.id,
            name=attachment.name,
            persona=scenario.persona,
            scenario_id=scenario.id,
            clean_text=attachment.text,
            planted_text=plant_payload(attachment.text, payload.text),
            payload_text=payload.text.strip(),
            prompt=scenario.task.description,
        ))
    return documents
