"""Run one scenario on one enforcement arm and report what the audience sees.

`scenario_runner.run_scenario` refuses the enforcement-off arm, for a reason
that is correct there and does not apply here: it judges scenarios, and a
scripted replay run unenforced would score as "the attack succeeded" when no
attack was mounted. This module judges nothing. It exists to *show* the
difference between the arms, so it must be able to run both.

What that costs is a labelling obligation, discharged by the caller: a
scripted run is not a model's own output, and presenting it as one would
misstate the finding. See the brief's §6.1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from policy_guard.upstream import connect_subprocess

from ..agent.adversarial import SYSTEM_PROMPT, task_for
from ..agent.loop import run_agent
from ..agent.transcript import CallOutcome
from ..run_setup import (
    off_arm_allowed,
    students_baseline_path,
    write_attachments,
    write_overlay,
    write_task,
)
from ..scenarios_spec import Scenario


class DemoCall(BaseModel):
    """One tool call, as the policy engine ruled on it and as the agent saw it.

    `verdict` and `blocked` are different questions and must not be collapsed.
    On the enforcement-off arm a call can be `verdict="deny"` and
    `blocked=False`: the engine objected and was overruled. That pair is the
    demonstration's whole point, so the model keeps both.
    """

    tool: str
    arguments: dict
    verdict: str
    policy: str
    reason: str
    blocked: bool
    #: True when the engine saw the call after an untrusted read. Separate
    #: from `verdict`: a tainted read-only call is allowed and still tainted.
    tainted: bool = False
    result: str


class DemoTurn(BaseModel):
    """One thing the model said. Empty for a scripted run, which has no model."""

    speaker: str
    text: str


class DemoRun(BaseModel):
    """One arm's run, as the page renders it.

    `enrolled_before` and `enrolled_after` are the demonstration's ground
    truth. They come from the run's own state file rather than from anything
    the agent said, for the reason `state.py` gives: scoring on final state
    rather than on model output is what keeps the claim free of judge noise.
    """

    scenario_id: str
    enforcement: str
    #: What produced the calls: `SCRIPTED`, or the model's name for a live run.
    #: The page renders this as its honesty label, so the two must not be
    #: indistinguishable.
    driver: str
    persona: str
    task: str
    payload_target: str | None
    payload_text: str | None
    calls: list[DemoCall]
    turns: list[DemoTurn] = []
    truncated: bool = False
    enrolled_before: list[str]
    enrolled_after: list[str]


#: What produced this run's tool calls. A scripted run replays the scenario's
#: own `replay` block: the calls are hand-written stand-ins for what an attack
#: would induce, not a model's output. Carried on the run itself so the page
#: cannot render one without the label. The live-model driver arrives in
#: phase 3; until then this is the only value.
SCRIPTED = "scripted"


class PersonaNotInPackError(ValueError):
    """The pack's dataset holds no record for the scenario's persona.

    Checked before the proxy starts, because the failure is otherwise silent
    in the worst possible direction: every call returns "no such student", so
    the enforcement-off arm mutates nothing, the two arms agree, and a
    misconfiguration renders as a clean enforcement result. See the brief's
    §6.2 -- this is how the Phase 0 probe first "passed".
    """


def _bounded(text: str, limit: int) -> str:
    """Cut `text` to `limit`, marking the cut. Follows the `[truncated]`
    convention `agent/loop.py` and `requisites.py` already use, rather than
    inventing a second one."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " [truncated]"


def _students(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _enrolled(students: dict, persona: str) -> list[str]:
    return list(students.get(persona, {}).get("enrolled", []))


def _read_decisions(run_dir: Path) -> list[dict]:
    path = run_dir / "decisions.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def _scripted(client, scenario) -> tuple[list[str], list[DemoTurn], bool]:
    """Issue the scenario's own `replay` calls. No model is involved."""
    results = []
    for call in scenario.replay:
        result = await client.call_tool(call.tool, call.arguments)
        body = "".join(part.text for part in result.content if hasattr(part, "text"))
        results.append(_bounded(body, 1500))
    return results, [], False


async def _live(client, scenario, *, chat, model, system_prompt) -> tuple[list[str], list[DemoTurn], bool]:
    """Let a model choose the calls, through the same loop the campaigns use.

    `agent.loop.run_agent` is reused rather than reimplemented: the demo must
    show the behaviour the experiments measured, and a second loop would be
    free to differ from it. Only the calls that reached the proxy are returned,
    in order -- a malformed or hallucinated proposal never produced a decision,
    so including it would misalign the two lists.
    """
    transcript = await run_agent(
        task_for(scenario, system_prompt=system_prompt), client, chat, model=model
    )
    reached = (CallOutcome.FORWARDED, CallOutcome.REFUSED)
    results = [
        record.detail
        for turn in transcript.turns
        for record in turn.calls
        if record.outcome in reached
    ]
    turns = [
        DemoTurn(speaker=model, text=turn.text)
        for turn in transcript.turns
        if turn.text.strip()
    ]
    return results, turns, transcript.truncated


async def run_demo(
    scenario: Scenario,
    *,
    enforcement: str,
    run_dir,
    pack_path,
    repo_root,
    chat=None,
    model: str | None = None,
    system_prompt: str = SYSTEM_PROMPT,
) -> DemoRun:
    """Run `scenario` through a proxy on `enforcement`, and report.

    With no `chat`, the scenario's scripted `replay` calls are issued. With
    one, a model chooses them and `model` names it. Everything downstream --
    the proxy, both policies, the enrolment server, the state file -- is
    identical either way, which is what lets the page show the same comparison
    under both drivers.
    """
    if chat is not None and not model:
        raise ValueError("a live run must name its model: `driver` is rendered as the page's honesty label")
    if enforcement == "off" and not off_arm_allowed(scenario):
        raise ValueError(
            f"{scenario.id} is marked deliverable: false, so it has no "
            f"enforcement-off arm to demonstrate: no mechanism delivers a "
            f"{scenario.payload.location if scenario.payload else 'n/a'} "
            f"payload. Showing one would claim a comparison the suite cannot "
            f"make."
        )

    baseline = students_baseline_path(pack_path, repo_root)
    before = _students(baseline)
    if scenario.persona not in before:
        raise PersonaNotInPackError(
            f"{scenario.id}: policy pack {Path(pack_path).name} resolves its "
            f"dataset to {baseline}, which holds no record for persona "
            f"{scenario.persona}. Every call would fail with 'no such student', "
            f"both arms would leave the enrolment unchanged, and the "
            f"demonstration would show no difference for a reason that has "
            f"nothing to do with enforcement. The scenario personas live in "
            f"the pack named by configs/enrolment-personas.yaml."
        )

    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    overlay_path = write_overlay(scenario, run_dir)
    attachments_path = write_attachments(scenario, run_dir)
    task_path = write_task(
        run_dir,
        task_id=scenario.id,
        description=scenario.task.description,
        allowed_tools=scenario.task.allowed_tools,
        principal_id=scenario.persona,
        overlay_path=overlay_path,
        attachments_path=attachments_path,
    )

    arguments = [
        "-m", "policy_guard",
        "--task", str(task_path),
        "--policy-pack", str(pack_path),
        "--run-dir", str(run_dir),
        "--enforcement", enforcement,
    ]

    #: What the agent received for each replayed call, in order: whether the
    #: call came back as an error, and the body it carried. Bounded because a
    #: study plan is long and the page shows it inline; `enforcement.refusal()`
    #: puts the whole deny reason first, so a block is never the thing clipped.
    async with connect_subprocess(sys.executable, arguments, cwd=str(repo_root)) as client:
        if chat is None:
            results, turns, truncated = await _scripted(client, scenario)
        else:
            results, turns, truncated = await _live(
                client, scenario, chat=chat, model=model, system_prompt=system_prompt
            )

    # `blocked` comes from the engine's own record rather than from the error
    # flag on the result: `forwarded` is the decision the policy layer took,
    # and it is the same field on both drivers. A result body can be an error
    # for reasons enforcement had nothing to do with.
    decisions = _read_decisions(run_dir)
    calls = [
        DemoCall(
            tool=decision["tool"],
            arguments=decision["arguments"],
            verdict=decision["verdict"],
            policy=decision["policy"],
            reason=decision["reason"],
            blocked=not decision["forwarded"],
            result=body,
        )
        for decision, body in zip(decisions, results, strict=True)
    ]
    return DemoRun(
        scenario_id=scenario.id,
        enforcement=enforcement,
        driver=model or SCRIPTED,
        persona=scenario.persona,
        task=scenario.task.description,
        payload_target=scenario.payload.target if scenario.payload else None,
        payload_text=scenario.payload.text if scenario.payload else None,
        calls=calls,
        turns=turns,
        truncated=truncated,
        enrolled_before=_enrolled(before, scenario.persona),
        enrolled_after=_enrolled(_students(run_dir / "state.json"), scenario.persona),
    )
