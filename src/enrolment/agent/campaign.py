"""Campaign orchestration: manifest, index, resume, replacement.

Domain-neutral by construction -- nothing here imports `scenarios_spec` or
anything else from the enrolment case study, so subsystem C (compliance runs)
reuses it unchanged. A "spec" is duck-typed: anything carrying `run_id`,
`attempt` and a `replacement()` method, plus the fields an `IndexRecord`
needs.

Nothing here judges a run. `run_status` is a projection of the transcript A
already wrote, and is the one place this subsystem could accidentally start
forming an opinion.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .transcript import ResponseOutcome, Transcript

TRANSCRIPT = "transcript.jsonl"
MANIFEST = "campaign.json"

#: The fields that define the method. Two halves of a campaign run under
#: different values of any of these are not one campaign, so resume refuses
#: (design §10). `fingerprints` is excluded because it is observed rather than
#: chosen -- re-reading a digest from a daemon must not invalidate a resume.
METHOD_FIELDS = (
    "models", "repeats", "system_prompt", "nudge",
    "max_model_turns", "max_calls", "temperature", "seed", "policy_pack",
)


class CampaignManifest(BaseModel):
    """What a later reader needs to know a number's provenance.

    The seed is recorded for completeness and must not be described as making
    runs reproducible: A's §7 measured temperature 0 giving greedy decoding on
    `llama3.1:8b` and not reliably on `llama3.2`, and the seed changing
    nothing on either.
    """

    models: list[str]
    repeats: int
    system_prompt: str
    nudge: str
    policy_pack: str
    max_model_turns: int = 4
    max_calls: int = 12
    temperature: float = 0.0
    seed: int | None = None
    fingerprints: dict = {}


def run_status(transcript: Transcript) -> str:
    """`ok`, `truncated` or `provider_error` -- read, never decided.

    A provider error also sets `truncated`, so it is checked first. The two
    are not interchangeable: a bound being reached is a fact about the model's
    behaviour and a result in its own right, while a dropped connection is
    infrastructure. Only the second is ever replaced (design §8).
    """
    if transcript.turns and transcript.turns[-1].outcome is ResponseOutcome.PROVIDER_ERROR:
        return "provider_error"
    if transcript.truncated:
        return "truncated"
    return "ok"


def existing_status(run_dir) -> str | None:
    """The status of a run already on disk, or None if there is none to read.

    Deliberately reads the transcript rather than the proxy's `complete`
    marker: the proxy writes that marker on clean shutdown regardless of
    whether the agent loop was truncated, so a bounded run and a finished one
    are indistinguishable at the marker level. A's §6 added the transcript
    header for exactly this.

    A transcript that cannot be parsed reads as "not run", so a run
    interrupted midway through being recorded is re-run rather than skipped.
    """
    path = Path(run_dir) / TRANSCRIPT
    if not path.exists():
        return None
    try:
        transcript = Transcript.read_jsonl(path)
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
    return run_status(transcript)
