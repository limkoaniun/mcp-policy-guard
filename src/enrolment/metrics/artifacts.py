"""What one run left on disk, and whether it may be scored.

Reading is separated from judging so that a missing or malformed artifact is
handled in one place, deliberately, rather than at every call site that
happens to touch it.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from policy_guard.config import load_policy_pack

from ..agent.campaign import IndexRecord
from ..agent.transcript import ResponseOutcome, Transcript

#: Every reason a run may be left out of a metric's denominator. Declared here
#: so a reason invented at a call site cannot become a population nothing
#: downstream knows to count -- the defect `ComplianceScenario`'s failure-mode
#: validator exists to prevent, in a different guise.
EXCLUSIONS = ("provider_error", "truncated", "not_complete", "no_transcript")


class RunArtifacts(BaseModel):
    """One run directory, read. Absent files are empty, not errors."""

    run_id: str
    transcript: Transcript | None = None
    decisions: list[dict] = []
    state: dict = {}
    erequests: list[dict] = []
    complete: bool = False


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_run(run_dir) -> RunArtifacts:
    """Read every artifact a run leaves behind.

    An absent `erequests.jsonl` means zero eRequests, not a broken run: the
    file is created on first append, so its absence is the ordinary case for a
    run that submitted nothing. An unreadable transcript reads as `None` rather
    than raising, because `exclusion_for` is where that becomes a decision.
    """
    run_dir = Path(run_dir)
    transcript_path = run_dir / "transcript.jsonl"
    transcript = None
    if transcript_path.exists():
        try:
            transcript = Transcript.read_jsonl(transcript_path)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            transcript = None

    state_path = run_dir / "state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (ValueError, TypeError):
            state = {}

    return RunArtifacts(
        run_id=run_dir.name,
        transcript=transcript,
        decisions=_read_jsonl(run_dir / "decisions.jsonl"),
        state=state,
        erequests=_read_jsonl(run_dir / "erequests.jsonl"),
        complete=(run_dir / "complete").exists(),
    )


def exclusion_for(record: IndexRecord, artifacts: RunArtifacts) -> str | None:
    """Why this run may not be scored, or None.

    Design §6. The order matters only for which reason is reported when
    several apply; `provider_error` comes first because it is the one that says
    nothing at all about the model.
    """
    if record.status == "provider_error":
        return "provider_error"
    if artifacts.transcript is None:
        return "no_transcript"
    if artifacts.transcript.turns and (
        artifacts.transcript.turns[-1].outcome is ResponseOutcome.PROVIDER_ERROR
    ):
        return "provider_error"
    if record.status == "truncated" or artifacts.transcript.truncated:
        return "truncated"
    if not artifacts.complete:
        return "not_complete"
    return None


def baseline_path(pack_path, repo_root) -> Path:
    """The student file a run's final state is compared against.

    Named by the pack's own `domain_rules_args["students_path"]`, never
    hardcoded: the adversarial pack points at `data/personas.json` and the
    compliance pack at `data/students.json`, and comparing a run against the
    wrong one would silently misjudge every final state. This mirrors
    `scenario_runner._students_baseline_path`, which exists for the same
    reason.
    """
    pack = load_policy_pack(Path(pack_path))
    students_path = pack.domain_rules_args.get("students_path")
    if students_path is None:
        raise ValueError(
            f"policy pack {pack.name!r} ({pack_path}) names no "
            f"domain_rules_args['students_path'], so there is no baseline to "
            f"compare a run's final state against"
        )
    path = Path(students_path)
    return path if path.is_absolute() else Path(repo_root) / path
