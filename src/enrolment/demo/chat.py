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

import anyio
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
    context: list[str] = []
    model: str = ""
    latency_ms: float = 0.0
    clarification: bool = False


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
            tainted=bool(d.get("tainted", False)), code=d.get("code"), result=_bounded(body, 1500),
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
        self.rag_mode = False
        self.rag_session: str | None = None
        self.pending_question: str | None = None
        self.overlay = overlay
        self.history: list[dict] = []
        self.turn = 0

        #: The dropped documents: id -> {"name", "text", "kind", "known"}.
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
        they are how the demonstration is staged. Held decisions go with it --
        a decision held against the record before the reset must not be
        approvable afterwards, or Allow would mutate a record it never held
        anything against.
        """
        shutil.copyfile(self.baseline_path, self.students_path)
        self.history = []
        self.erequests = []
        self.turns = {}
        self.rag_session = None
        self.pending_question = None

    @property
    def mode(self) -> str:
        return "rag" if self.rag_mode else f"agent-{self.enforcement}"

    def set_mode(self, mode: str) -> None:
        if mode not in ("rag", "agent-on", "agent-off"):
            raise ValueError("mode must be rag, agent-on, or agent-off")
        if self.busy:
            raise ValueError("a message is running; wait before switching modes")
        if mode != self.mode:
            self.reset()
            self.rag_mode = mode == "rag"
            if not self.rag_mode:
                self.enforcement = mode.removeprefix("agent-")

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
        """Only `name` and `text` reach the proxy. `kind` and `known` are the
        page's, and the tool must not see a tag saying which scenario a
        document is."""
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
            if self.rag_mode:
                return await self._send_rag(message, chat=chat, model=model, on_event=on_event)
            return await self._send(message, chat=chat, model=model, on_event=on_event)
        finally:
            self.busy = False

    async def _send_rag(self, message: str, *, chat, model: str, on_event=None) -> ChatReplyOut:
        from ..agent.advice import load_world
        from ..rag import answer
        from .rag_chat import resolve_question

        world = load_world(self.repo_root, self.students_path)
        query, session, clarification = resolve_question(
            message, history=self.history, catalogue=world.catalogue,
            previous_session=self.rag_session, pending=self.pending_question,
        )
        self.turn += 1
        run_dir = self.root / f"turn-{self.turn:03d}"
        run_dir.mkdir(parents=True, exist_ok=False)
        self._write_attachments(run_dir)
        context, messages, latency = [], [], 0.0
        try:
            if clarification:
                text = clarification
            else:
                reply = await anyio.to_thread.run_sync(lambda: answer(
                    message, student=world.students[self.persona], catalogue=world.catalogue,
                    requisites=world.requisites, course=world.course, aos=world.aos,
                    chat=chat, model=model, attachments=self.attachments, session=session,
                    history=self.history, retrieval_question=query, max_context_chars=24000,
                    strict_response=True,
                ))
                text, context, latency, messages = reply.text, reply.context, reply.latency_ms, reply.messages
        except Exception as error:
            (run_dir / "rag.json").write_text(json.dumps({
                "mode": "rag", "model": model, "question": message, "error": str(error),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            raise
        artifact = {"mode": "rag", "model": model, "question": message,
                    "retrieval_question": query, "session": session, "text": text,
                    "context": context, "messages": messages, "latency_ms": latency,
                    "clarification": bool(clarification)}
        (run_dir / "rag.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        self.pending_question = (self.pending_question or message) if clarification else None
        if session:
            self.rag_session = session
        self.history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": text}])
        if on_event:
            on_event({"kind": "said", "text": text, "mode": "rag", "model": model,
                      "clarification": bool(clarification)})
        return ChatReplyOut(said=[text], turn=self.turn, context=context, model=model,
                            latency_ms=latency, clarification=bool(clarification))

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
        # No overlay_path here: the app never gives a session one. A session
        # started with `overlay=...` would need the replay to write it too,
        # the way `_send` does, but nothing today constructs one that way.
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

        # Check the re-issued reads landed before anything moves. A fresh
        # proxy re-reads each untrusted document so the taint gate sees the
        # same history it would have live; if a re-read is itself refused
        # (the allowlist changed since the turn, say), the held call would
        # run untainted and the confirmation would report it as reached
        # cleanly by the allowlist -- a record of an event that did not
        # happen. Catching it here, before `state.json` is copied over
        # `students.json` or eRequests are collected, keeps a failed replay
        # a no-op: nothing was confirmed, so nothing should have moved.
        replayed = _read_decisions(run_dir)
        for decision in replayed[:-1]:
            if not decision["forwarded"]:
                raise ValueError(
                    f"the replay could not re-read {decision['tool']!r} "
                    f"({decision['reason']}); nothing was confirmed"
                )

        state_path = run_dir / "state.json"
        if state_path.exists():
            shutil.copyfile(state_path, self.students_path)
        self._collect_erequests(run_dir, turn=turn, confirmed=True)

        calls = _calls_from(replayed, results)
        return Confirmation(turn=turn, index=index, reread=len(reads), call=calls[-1])
