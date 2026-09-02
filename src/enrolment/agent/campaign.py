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


INDEX = "index.jsonl"


class CampaignMismatchError(ValueError):
    """A campaign being resumed under a different method than it started."""


class IndexRecord(BaseModel):
    """One row of what was run. Not a judgement: `status` is the projection
    `run_status` made, and nothing here says whether an attack succeeded."""

    run_id: str
    scenario_id: str
    attack_class: str | None = None
    model: str
    arm: str
    repeat: int
    attempt: int = 0
    status: str
    duration_s: float = 0.0
    #: Set on a replacement, naming the provider-error run it stands in for.
    replaces: str | None = None
    #: Set on the failure, naming the run that replaced it. Both directions
    #: are recorded so D counts the replacement and a reader sees the failure.
    replaced_by: str | None = None


def write_index(campaign_dir, records: list[IndexRecord]) -> Path:
    """Rewrite the index whole.

    Not append-only: a replacement gives its predecessor a `replaced_by`
    after that record was first written, and appending would leave the stale
    copy behind for a reader to trip over. The file is small -- one short line
    per run -- so rewriting it after every run costs nothing and means a
    campaign killed mid-flight still leaves a consistent index.
    """
    campaign_dir = Path(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_dir / INDEX
    lines = [json.dumps(r.model_dump(), ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def read_index(campaign_dir) -> list[IndexRecord]:
    path = Path(campaign_dir) / INDEX
    if not path.exists():
        return []
    return [
        IndexRecord.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_or_create_manifest(campaign_dir, manifest: CampaignManifest) -> CampaignManifest:
    """Write the manifest for a new campaign, or check it for a resumed one.

    A campaign resumed under a different system prompt, model list, repeat
    count or bound is not the same campaign, and the two halves are not
    comparable. Refused rather than merged. `fingerprints` is exempt: a digest
    is observed rather than chosen, and re-reading one from the daemon must
    not read as a change of method.
    """
    campaign_dir = Path(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_dir / MANIFEST

    if not path.exists():
        path.write_text(json.dumps(manifest.model_dump(), indent=2), encoding="utf-8")
        return manifest

    stored = CampaignManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
    differences = [
        f"{field}: campaign has {getattr(stored, field)!r}, "
        f"invoked with {getattr(manifest, field)!r}"
        for field in METHOD_FIELDS
        if getattr(stored, field) != getattr(manifest, field)
    ]
    if differences:
        raise CampaignMismatchError(
            f"{path} was started under a different method and cannot be resumed "
            f"under this one -- half a campaign run each way is not a campaign. "
            + "; ".join(differences)
        )
    return stored
