"""Campaign orchestration: manifest, index, resume, replacement.

Domain-neutral by construction -- nothing here imports `scenarios_spec` or
anything else from the enrolment case study, so subsystem C (compliance runs)
reuses it unchanged. `run_campaign` is written against `RunSpec`, defined
below, but the contract it actually relies on is structural: anything
carrying `run_id`, `attempt` and a `replacement()` method, plus the fields an
`IndexRecord` needs, works -- so another subsystem may pass its own rather
than this one.

Nothing here judges a run. `run_status` is a projection of the transcript A
already wrote, and is the one place this subsystem could accidentally start
forming an opinion.
"""

from __future__ import annotations

import json
import re
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
    "sample_digest",
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
    #: Which sample this campaign drew, when it drew one. `None` for a suite
    #: that is not sampled (the adversarial one runs all sixteen scenarios).
    #: In METHOD_FIELDS because half a campaign over one sample and half over
    #: another is not a campaign.
    sample_digest: str | None = None


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
    `TypeError` is in that set because a line of valid JSON that is not an
    object -- a truncated write can leave one -- is subscripted, not
    validated, and raises there rather than as a `ValueError`.
    """
    path = Path(run_dir) / TRANSCRIPT
    if not path.exists():
        return None
    try:
        transcript = Transcript.read_jsonl(path)
    except (ValueError, KeyError, TypeError):
        return None
    return run_status(transcript)


def clear_run_dir(run_dir: Path) -> None:
    """Make way for a fresh run, or refuse to.

    A run directory that already exists is one of two things, and conflating
    them cost design §9's promise that resume "continues where it stopped".

    **No readable transcript.** `run_one` creates the directory first and
    writes `transcript.jsonl` last, after the proxy has shut down, so this is
    exactly what killing a 280-run campaign mid-run leaves behind -- the
    commonest way this campaign stops. It is an ordinary interrupted run, and
    `campaign.existing_status` already reads it as "not run" and asks for it
    again; refusing to make it would leave resume unable to do the one thing
    it exists for. The directory is moved aside rather than deleted: its
    partial `decisions.jsonl` is evidence, and this subsystem destroys none.

    **A readable transcript.** The run finished. Being asked to make it again
    can only mean the resume logic believed the directory was fresh when it
    was not, and that must surface -- swallowing it would produce exactly the
    log-and-state divergence `RunState` raises `RunDirectoryInUseError` to
    prevent.
    """
    if not run_dir.exists():
        return
    if existing_status(run_dir) is not None:
        raise FileExistsError(
            f"{run_dir} already holds a readable transcript, so the resume "
            f"logic asked for a run that had already been made. Not retried "
            f"and not moved: that would hide a divergence between what the "
            f"index says was run and what is on disk."
        )
    attempt = 1
    while (aside := run_dir.with_name(f"{run_dir.name}.interrupted-{attempt}")).exists():
        attempt += 1
    run_dir.rename(aside)
    print(
        f"{run_dir.name}: an interrupted run with no transcript was moved to "
        f"{aside.name} and is being run again. Nothing was deleted."
    )


#: Replacement attempts get a letter, so `...-r1` is followed by `...-r1a`.
#: Two is the bound design §8 sets; beyond it the cell is left short and D
#: reports the reduced N rather than B trying harder.
_ATTEMPT_SUFFIXES = ("", "a", "b")


def model_slug(model: str) -> str:
    """`model` reduced to characters safe in a directory name on any filesystem.

    `llama3.1:8b` becomes `llama318b`. The colon alone would be enough reason
    -- it is a path separator on some platforms and reserved on others.
    """
    return re.sub(r"[^a-z0-9]", "", model.lower())


class RunSpec(BaseModel):
    """One cell of the matrix: one scenario, one model, one arm, one repeat."""

    scenario_id: str
    model: str
    arm: str
    repeat: int
    attempt: int = 0

    @property
    def run_id(self) -> str:
        suffix = _ATTEMPT_SUFFIXES[self.attempt]
        return f"{self.scenario_id}-{model_slug(self.model)}-{self.arm}-r{self.repeat}{suffix}"

    def replacement(self) -> RunSpec:
        """The next attempt at this cell, in a directory of its own.

        Run directories are single-use -- `RunState.load` raises
        `RunDirectoryInUseError` on a second load -- so a replacement can
        never be a re-entry into the failed one, and B does not try to make it
        one.
        """
        return self.model_copy(update={"attempt": self.attempt + 1})


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
    #: Wall clock, or `None` for a run adopted from disk that no invocation
    #: is able to say it timed. Optional rather than `0.0` for the reason
    #: `ollama._provider_latency_ms` gives: a default zero renders a
    #: measurement that did not happen as one that did, in the artifact the
    #: scoring subsystem reads.
    duration_s: float | None = None
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

    Written to a sibling and renamed over the top, because "killed mid-flight"
    is the design's expectation rather than an edge case: an in-place rewrite
    truncates first, so a kill landing in the wrong microsecond would turn the
    campaign's own record of itself into a fragment.
    """
    campaign_dir = Path(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_dir / INDEX
    lines = [json.dumps(r.model_dump(), ensure_ascii=False) for r in records]
    scratch = path.with_suffix(path.suffix + ".tmp")
    scratch.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    scratch.replace(path)
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


async def run_campaign(
    specs,
    runner,
    *,
    campaign_dir,
    describe,
    max_replacements: int = 2,
) -> list[IndexRecord]:
    """Run every spec that has not already been run, and index the results.

    Written against `RunSpec`, defined above, but `spec` is accepted
    structurally -- anything carrying `run_id`, `attempt` and a
    `replacement()` method will do, so another subsystem may pass its own.

    `runner` is `async (spec) -> (status, duration_s)`; `describe` is
    `(spec) -> dict` supplying an `IndexRecord`'s domain fields. Both are
    injected so this module stays free of any domain import and subsystem C
    can reuse it unchanged.

    Two rules decide what actually runs.

    **Resume** (design §9): a spec whose run directory already holds a
    readable transcript is not re-run -- unless its status is
    `provider_error`, which is not a completed run but the trigger below. The
    check reads the transcript, never the proxy's `complete` marker, which is
    written on clean shutdown regardless of truncation.

    **Replacement** (design §8): a `provider_error` is infrastructure, not
    model behaviour, and left alone it shrinks a repeat group for a reason
    unrelated to the experiment. So a fresh run is made in a new directory,
    bounded at `max_replacements`, with the failure kept on disk and the
    relationship recorded in both directions. A `truncated` run is never
    replaced: a bound being reached is a result.

    **Prior rows are merged, not discarded.** The index is rewritten whole,
    so an invocation that derived it from `specs` alone would delete the row
    of every run outside this invocation's slice -- which `--limit` produces
    by design, and which a resume produces for any run whose spec list has
    since been capped. The run directories survive, but the index is what the
    scoring subsystem reads. Merging is also what lets an adopted run keep the
    duration that was actually measured for it: re-deriving that row at zero
    would put a wall clock that never elapsed into D's input, and design §9
    says this campaign *will* be resumed.

    Exceptions from `runner` are not caught. A run directory that already
    exists and holds a readable transcript means the resume logic believed it
    was fresh when it was not, and that must surface.
    """
    campaign_dir = Path(campaign_dir)
    # Insertion-ordered, and seeded from disk: overwriting a key keeps its
    # original position, so a resumed campaign's index stays in run order
    # rather than being reshuffled by which runs this invocation touched.
    records: dict[str, IndexRecord] = {r.run_id: r for r in read_index(campaign_dir)}

    def flush() -> None:
        write_index(campaign_dir, list(records.values()))

    async def execute(spec) -> IndexRecord:
        """Run `spec`, or adopt the run already sitting on disk for it."""
        prior = records.get(spec.run_id)
        found = existing_status(campaign_dir / spec.run_id)
        if found is None:
            status, duration = await runner(spec)
            replaces, replaced_by = None, None
        else:
            # Nothing here timed this run. If an earlier invocation did, its
            # figure is carried forward; if none did, the field stays None,
            # which says "not measured" rather than "instant".
            status = found
            duration = prior.duration_s if prior is not None else None
            replaces = prior.replaces if prior is not None else None
            replaced_by = prior.replaced_by if prior is not None else None
        record = IndexRecord(run_id=spec.run_id, status=status, duration_s=duration,
                             replaces=replaces, replaced_by=replaced_by, **describe(spec))
        records[record.run_id] = record
        flush()
        return record

    for spec in specs:
        current, record = spec, await execute(spec)

        while record.status == "provider_error" and current.attempt < max_replacements:
            current = current.replacement()
            replacement = await execute(current)
            record.replaced_by = replacement.run_id
            replacement.replaces = record.run_id
            flush()
            record = replacement

    return list(records.values())
