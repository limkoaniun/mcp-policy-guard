"""A live chat session against the policy layer.

`demo.session` runs one scenario end to end. This holds a *conversation*: many
messages against one accumulating student record, with the allowlist and the
enforcement switch changeable between messages.

**One short-lived proxy per message, seeded from the record the last message
left behind.** That choice does three things at once:

- a mid-conversation allowlist or enforcement change costs nothing. The next
  message starts a proxy that was told something different; there is no
  running process to reconfigure.
- `state.RunDirectoryInUseError`'s single-use rule stays intact rather than
  being worked around. Every message gets its own run directory, and its
  baseline is the previous message's `state.json`.
- every message leaves the same artifacts a campaign run does --
  `decisions.jsonl`, `state.json`, `manifest.json` -- so what the page shows is
  the same evidence the experiments were scored from.

The cost is a subprocess spawn per message, about a second against a model
call of ten to thirty. That is not the bottleneck.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel

from policy_guard.upstream import connect_subprocess

from ..agent.adversarial import SYSTEM_PROMPT
from ..agent.loop import AgentTask, run_agent
from ..run_setup import students_baseline_path, write_task
from .session import DemoCall, _bounded, _read_decisions


class ChatReplyOut(BaseModel):
    """What one message produced."""

    said: list[str] = []
    calls: list[DemoCall] = []
    truncated: bool = False
    turn: int = 0


class Confirmation(BaseModel):
    """What approving one held call did."""

    turn: int
    index: int
    #: How many untrusted reads were re-issued before the held call, so the
    #: page can say "re-read 1 document, then forwarded" rather than show one
    #: call when two ran.
    reread: int
    call: DemoCall


def _calls_from(decisions: list[dict], results: list[str]) -> list[DemoCall]:
    """Pair decision records with what the agent received, in order.

    `results` is what the agent received; a proposal that never reached the
    proxy produced no decision, so the two align. Shared by `_send` and
    `approve` so the pairing rule lives in one place.
    """
    return [
        DemoCall(
            tool=d["tool"], arguments=d["arguments"], verdict=d["verdict"],
            policy=d["policy"], reason=d["reason"], blocked=not d["forwarded"],
            tainted=bool(d.get("tainted", False)), result=_bounded(body, 1500),
        )
        for d, body in zip(decisions, results, strict=True)
    ]


class ChatSession:
    def __init__(
        self,
        *,
        persona: str,
        root,
        pack_path,
        repo_root,
        allowed_tools: list[str],
        enforcement: str = "on",
        overlay: dict | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.persona = persona
        self.root = Path(root)
        self.repo_root = Path(repo_root)
        self.allowed_tools = list(allowed_tools)
        self.enforcement = enforcement
        self.overlay = overlay
        self.history: list[dict] = []
        self.turn = 0

        #: The attach control's documents: id -> {"name", "text", "variant"}.
        #: Written to each turn's `attachments.json` so the upstream serves
        #: exactly this text. Survives `reset` -- it is staging, like the
        #: allowlist.
        self.attachments: dict[str, dict] = {}
        #: Every eRequest the upstream recorded, across turns. The server
        #: appends to a per-run `erequests.jsonl`; a conversation spans runs,
        #: so the session accumulates them here. Cleared by `reset` with the
        #: record, because a request is record-like: it is something that
        #: happened to the student.
        self.erequests: list[dict] = []
        #: What each turn was and decided, kept for `approve`: the message,
        #: the allowlist at the time, and the decision records.
        self.turns: dict[int, dict] = {}
        self.busy = False

        self.root.mkdir(parents=True, exist_ok=True)
        self.system_prompt = system_prompt.replace("{principal}", persona)

        # The session's own students file: the record every message starts
        # from, and the one each message's result is written back to. Seeded
        # from the pack's baseline so the conversation begins where a campaign
        # run would.
        self.students_path = self.root / "students.json"
        # Kept so `reset` can put the record back. The session's own students
        # file is written over as the conversation mutates it, so the baseline
        # has to be remembered rather than re-derived from it.
        self.baseline_path = students_baseline_path(pack_path, repo_root)
        shutil.copyfile(self.baseline_path, self.students_path)

        # The session's own pack, pointing at that file. Derived from the real
        # pack rather than hand-written, for the reason `ablation` gives: a
        # copy is free to drift, and then the demo shows something the
        # experiments did not run.
        body = yaml.safe_load(Path(pack_path).read_text(encoding="utf-8")) or {}
        body["name"] = f"{body.get('name', 'pack')}-chat"
        body.setdefault("domain_rules_args", {})["students_path"] = str(self.students_path)
        self.pack_path = self.root / "pack.yaml"
        self.pack_path.write_text(
            "# Generated per chat session. The students file is the session's own,\n"
            "# so each message starts from the record the last one left behind.\n"
            + yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

        # The tools whose forwarded results taint the run, by the pack's own
        # labels -- read here so `approve` can re-issue exactly those reads.
        self.untrusted_tools = frozenset(
            tool for tool, label in (body.get("trust") or {}).items() if label == "untrusted"
        )

    # ------------------------------------------------------------------ reset
    def reset(self) -> None:
        """Put the record back and forget the conversation.

        Spec §8 calls this non-negotiable: every interesting demonstration
        mutates the record, and a second run starting from the damage the
        first one did shows something different for a reason nobody watching
        can see.

        The allowlist and the enforcement switch survive. They are how a
        demonstration is staged, and losing them on every reset would make the
        button hostile to the thing it exists for. Turn directories from
        before the reset are left on disk -- they are the evidence of what
        happened, and this module does not delete evidence.

        The eRequest log goes back with the record; the attachments stay,
        they are how the demonstration is staged.
        """
        shutil.copyfile(self.baseline_path, self.students_path)
        self.history = []
        self.erequests = []

    # ------------------------------------------------------------------ state
    def record(self) -> dict:
        """The student's record as it stands, for the live panel."""
        students = json.loads(self.students_path.read_text(encoding="utf-8"))
        held = students.get(self.persona) or {}
        # `completed` holds `{code, session, credit_points}` records, not bare
        # codes -- the requisite evaluator needs the session and the points.
        # The panel wants codes, so the shape is flattened here rather than in
        # the page: a renderer guessing at a domain model is how
        # `[object Object]` reaches a demonstration.
        completed = [
            entry.get("code") if isinstance(entry, dict) else entry
            for entry in (held.get("completed") or [])
        ]
        return {
            "student_id": self.persona,
            "completed": completed,
            "enrolled": list(held.get("enrolled") or []),
            "credit_cap": held.get("credit_point_cap_per_session"),
            "course": held.get("course"),
            "major": held.get("major"),
        }

    # ------------------------------------------------------------ attachments
    def _write_attachments(self, run_dir: Path) -> Path | None:
        if not self.attachments:
            return None
        path = run_dir / "attachments.json"
        documents = {
            attachment_id: {"name": entry["name"], "text": entry["text"]}
            for attachment_id, entry in self.attachments.items()
        }
        path.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _collect_erequests(self, run_dir: Path, *, turn: int, confirmed: bool = False) -> None:
        path = run_dir / "erequests.jsonl"
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self.erequests.append({**json.loads(line), "turn": turn, "confirmed": confirmed})

    # ------------------------------------------------------------------- send
    async def send(self, message: str, *, chat, model: str, on_event=None) -> ChatReplyOut:
        """Run one message through a proxy carrying the session's current settings.

        `busy` is shared with `approve`: both write `students.json`, so a
        message and an approval running at once would race to that file.
        """
        if self.busy:
            raise RuntimeError("the session is busy with another message or an approval")
        self.busy = True
        try:
            return await self._send(message, chat=chat, model=model, on_event=on_event)
        finally:
            self.busy = False

    async def _send(self, message: str, *, chat, model: str, on_event=None) -> ChatReplyOut:
        self.turn += 1
        run_dir = self.root / f"turn-{self.turn:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)

        overlay_path = None
        if self.overlay:
            overlay_path = run_dir / "overlay.yaml"
            overlay_path.write_text(
                yaml.safe_dump(self.overlay, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

        task_path = write_task(
            run_dir,
            task_id=f"chat-{self.turn:03d}",
            description=message,
            allowed_tools=self.allowed_tools,
            principal_id=self.persona,
            overlay_path=overlay_path,
            attachments_path=self._write_attachments(run_dir),
        )

        arguments = [
            "-m", "policy_guard",
            "--task", str(task_path),
            "--policy-pack", str(self.pack_path),
            "--run-dir", str(run_dir),
            "--enforcement", self.enforcement,
        ]

        said: list[str] = []
        results: list[str] = []

        def record_event(event):
            if event["kind"] == "said" and event.get("text", "").strip():
                said.append(event["text"])
            if event["kind"] == "call":
                results.append(event.get("detail", ""))
            if on_event is not None:
                on_event(event)

        task = AgentTask(task_id=f"chat-{self.turn:03d}", system=self.system_prompt,
                         turns=[message])
        async with connect_subprocess(sys.executable, arguments, cwd=str(self.repo_root)) as client:
            transcript = await run_agent(
                task, client, chat, model=model,
                history=self.history, on_event=record_event,
            )

        # The record this message left behind becomes the next one's baseline.
        state_path = run_dir / "state.json"
        if state_path.exists():
            shutil.copyfile(state_path, self.students_path)
        self._collect_erequests(run_dir, turn=self.turn)

        decisions = _read_decisions(run_dir)
        self.turns[self.turn] = {
            "message": message,
            "allowed_tools": list(self.allowed_tools),
            "decisions": decisions,
        }
        calls = _calls_from(decisions, results)
        return ChatReplyOut(said=said, calls=calls, truncated=transcript.truncated, turn=self.turn)

    # ---------------------------------------------------------------- approve
    async def approve(self, *, turn: int, index: int) -> Confirmation:
        """Replay one held call with the student's confirmation.

        Upload-channel design §3.4: confirming replays the call through a
        fresh proxy started with `--approval-mode approve`, on the same task.
        A fresh proxy is untainted, so the turn's forwarded reads of untrusted
        tools are re-issued first; otherwise the taint gate would not fire and
        the log would record an `allow` by the allowlist for an event that
        never happened. The replay leaves the same artifacts a turn does, in
        `turn-NNN-approval`, and its state becomes the session's.

        Holds `busy` for the same reason `send` does: both write
        `students.json`, and a `send` and an `approve` racing on that file
        would leave the record showing whichever one happened to finish last.
        """
        if self.busy:
            raise ValueError("a message is still running; wait for it to finish")
        self.busy = True
        try:
            return await self._approve(turn=turn, index=index)
        finally:
            self.busy = False

    async def _approve(self, *, turn: int, index: int) -> Confirmation:
        record = self.turns.get(turn)
        if record is None:
            raise LookupError(f"no turn {turn}")
        decisions = record["decisions"]
        if not 0 <= index < len(decisions):
            raise LookupError(f"turn {turn} has no decision {index}")
        held = decisions[index]
        if held["verdict"] != "require_approval" or held["forwarded"]:
            raise ValueError(
                f"decision {index} of turn {turn} was not held: verdict {held['verdict']!r}, "
                f"forwarded={held['forwarded']}"
            )
        run_dir = self.root / f"turn-{turn:03d}-approval"
        if run_dir.exists():
            raise ValueError(f"turn {turn} was already confirmed")
        run_dir.mkdir(parents=True)

        reads = [
            d for d in decisions[:index]
            if d["forwarded"] and d["tool"] in self.untrusted_tools
        ]
        source = self.root / f"turn-{turn:03d}" / "attachments.json"
        attachments_path = None
        if source.exists():
            attachments_path = run_dir / "attachments.json"
            shutil.copyfile(source, attachments_path)
        task_path = write_task(
            run_dir,
            task_id=f"chat-{turn:03d}-approval",
            description=record["message"],
            allowed_tools=record["allowed_tools"],
            principal_id=self.persona,
            attachments_path=attachments_path,
        )
        arguments = [
            "-m", "policy_guard",
            "--task", str(task_path),
            "--policy-pack", str(self.pack_path),
            "--run-dir", str(run_dir),
            "--enforcement", self.enforcement,
            "--approval-mode", "approve",
        ]

        results: list[str] = []
        async with connect_subprocess(sys.executable, arguments, cwd=str(self.repo_root)) as client:
            for call in [*reads, held]:
                result = await client.call_tool(call["tool"], call["arguments"])
                results.append("".join(
                    part.text for part in result.content if hasattr(part, "text")))

        state_path = run_dir / "state.json"
        if state_path.exists():
            shutil.copyfile(state_path, self.students_path)
        self._collect_erequests(run_dir, turn=turn, confirmed=True)

        replayed = _read_decisions(run_dir)
        calls = _calls_from(replayed, results)
        return Confirmation(turn=turn, index=index, reread=len(reads), call=calls[-1])
