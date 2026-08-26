"""The decision log.

Every verdict the proxy reaches is appended here as one JSON object per line.
The evaluation harness computes attack success and rule violation rates from
this file, and the demonstration frontend renders from it — which is what keeps
the frontend from needing any backend of its own.

`forwarded` is the field that matters most: it records whether the proxy
*decided* to forward the call rather than refuse it, which is the property the
whole enforcement design rests on. Note the precise wording — the record is
written when the decision is reached, before the call is attempted upstream, so
`forwarded: true` means "not refused by policy", not "the upstream received
this". Splitting it into a decision field and an outcome field needs the
post-call hook and belongs to a later slice; until then, an upstream that dies
mid-task is caught by the run marker below rather than by this field.

## Scoring rule: an unmarked run is not a result

`runs/<id>/complete` is written only when the proxy shuts down cleanly.
`state.json`'s existence says the run *started*; this marker says it
*finished*. A run that dies after three of eight scripted calls leaves a
well-formed decision log and a parseable state file, and if the attack was
call five, a harness reading those two artifacts alone sees no violation and
scores a successful defence.

**A run directory lacking `complete` must be excluded from scoring, never read
as "state unchanged, so the attack was blocked."**

What the marker does *not* attest, stated plainly so nothing reads more into it
than it carries: it says the **proxy** shut down cleanly, not that the
**agent** finished its script. An agent that dies partway through disconnects,
which the proxy cannot tell from an agent that finished, so the run is marked
complete. A harness must therefore check its own agent's exit as well as this
marker before scoring a run. Closing that gap needs a signal from the agent
side and belongs with the evaluation harness, which has its own spec.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Decision, ToolCall

RUN_COMPLETE_MARKER = "complete"


def mark_run_complete(run_dir) -> Path:
    """Write the end-of-run marker into `run_dir`. See the module docstring.

    Called once, at clean shutdown. Anything that stops the proxy earlier --
    a dead upstream, a crash, a kill -- leaves the marker absent, which is
    exactly the signal the harness needs to exclude the run.
    """
    path = Path(run_dir) / RUN_COMPLETE_MARKER
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        datetime.now(UTC).isoformat(timespec="milliseconds") + "\n", encoding="utf-8"
    )
    return path


def run_is_complete(run_dir) -> bool:
    """Whether `run_dir` holds a run the harness may score."""
    return (Path(run_dir) / RUN_COMPLETE_MARKER).exists()


class DecisionRecord(BaseModel):
    ts: str
    task_id: str
    tool: str
    arguments: dict = Field(default_factory=dict)
    verdict: str
    policy: str
    reason: str
    tainted: bool = False
    approval_mode: str | None = None
    latency_ms: float = 0.0
    forwarded: bool = False


class DecisionLog:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path is not None else None
        self.records: list[DecisionRecord] = []
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def persistent(self) -> bool:
        """Whether decisions are being written to disk.

        False means this log keeps records in memory only. That is legitimate
        for unit tests, and wrong for a run whose decision log is the evidence
        the evaluation harness reads — a caller that needs a durable audit
        trail should assert on this rather than assume it.
        """
        return self.path is not None

    def record(
        self,
        call: ToolCall,
        decision: Decision,
        *,
        task_id: str,
        forwarded: bool,
        latency_ms: float,
        tainted: bool = False,
        approval_mode: str | None = None,
        now: str | None = None,
    ) -> DecisionRecord:
        record = DecisionRecord(
            ts=now or datetime.now(UTC).isoformat(timespec="milliseconds"),
            task_id=task_id,
            tool=call.tool,
            arguments=call.arguments,
            verdict=decision.verdict.value,
            policy=decision.policy,
            reason=decision.reason,
            tainted=tainted,
            approval_mode=approval_mode,
            latency_ms=latency_ms,
            forwarded=forwarded,
        )
        line = json.dumps(record.model_dump(), ensure_ascii=False, default=repr)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        self.records.append(record)
        return record
