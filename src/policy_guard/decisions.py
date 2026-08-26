"""The decision log.

Every verdict the proxy reaches is appended here as one JSON object per line.
The evaluation harness computes attack success and rule violation rates from
this file, and the demonstration frontend renders from it — which is what keeps
the frontend from needing any backend of its own.

`forwarded` is the field that matters most: it records whether the call
actually reached the upstream server, which is the property the whole
enforcement design rests on.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Decision, ToolCall


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
