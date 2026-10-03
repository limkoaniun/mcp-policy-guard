"""Build a run directory on disk, so every verdict path is testable with no
proxy, no model and no campaign.

Subsystem D reads seven artifacts and has to behave sensibly when any of them
is missing. Hand-built runs are the only way to reach the missing-artifact
paths deliberately rather than by waiting for one to happen.
"""

from __future__ import annotations

import json
from pathlib import Path

from enrolment.agent.transcript import (
    CallOutcome,
    CallRecord,
    ResponseOutcome,
    Transcript,
    TurnRecord,
)


def call(tool: str, outcome: CallOutcome = CallOutcome.FORWARDED, **arguments) -> CallRecord:
    return CallRecord(tool=tool, arguments=arguments, outcome=outcome)


def write_run(
    campaign_dir,
    run_id: str,
    *,
    calls: list[CallRecord] | None = None,
    truncated: bool = False,
    provider_error: bool = False,
    enrolled: dict[str, list[str]] | None = None,
    erequests: list[dict] | None = None,
    decisions: list[dict] | None = None,
    complete: bool = True,
    transcript: bool = True,
    text: str = "",
) -> Path:
    """Write one run directory. Every argument has a defensible default so a
    test names only what it is about."""
    run_dir = Path(campaign_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if transcript:
        turns = [
            TurnRecord(
                user_turn=0,
                model_turn=0,
                outcome=(
                    ResponseOutcome.PROVIDER_ERROR if provider_error
                    else ResponseOutcome.TOOL_CALLS if calls
                    else ResponseOutcome.TEXT
                ),
                text=text,
                calls=list(calls or []),
            )
        ]
        Transcript(
            task_id=run_id.split("-")[0],
            model="fake",
            turns=turns,
            truncated=truncated or provider_error,
            truncation_reason="reason" if truncated or provider_error else None,
        ).write_jsonl(run_dir / "transcript.jsonl")

    if enrolled is not None:
        state = {
            student: {"student_id": student, "course": "C04296",
                      "admitted_to": ["C04296"], "enrolled": codes}
            for student, codes in enrolled.items()
        }
        (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    if erequests is not None:
        (run_dir / "erequests.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in erequests), encoding="utf-8"
        )

    if decisions is not None:
        (run_dir / "decisions.jsonl").write_text(
            "".join(json.dumps(d) + "\n" for d in decisions), encoding="utf-8"
        )

    if complete:
        (run_dir / "complete").write_text("", encoding="utf-8")

    return run_dir


def decision(tool: str, *, latency_ms: float = 0.1, verdict: str = "allow",
             forwarded: bool = True) -> dict:
    return {
        "ts": "2026-09-03T00:00:00.000+00:00", "task_id": "T", "tool": tool,
        "arguments": {}, "verdict": verdict, "policy": "allowlist", "reason": "",
        "tainted": False, "approval_mode": None, "latency_ms": latency_ms,
        "forwarded": forwarded,
    }
