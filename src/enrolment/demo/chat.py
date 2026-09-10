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

        self.root.mkdir(parents=True, exist_ok=True)
        self.system_prompt = system_prompt.replace("{principal}", persona)

        # The session's own students file: the record every message starts
        # from, and the one each message's result is written back to. Seeded
        # from the pack's baseline so the conversation begins where a campaign
        # run would.
        self.students_path = self.root / "students.json"
        shutil.copyfile(students_baseline_path(pack_path, repo_root), self.students_path)

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

    # ------------------------------------------------------------------- send
    async def send(self, message: str, *, chat, model: str, on_event=None) -> ChatReplyOut:
        """Run one message through a proxy carrying the session's current settings."""
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

        decisions = _read_decisions(run_dir)
        calls = [
            DemoCall(
                tool=d["tool"], arguments=d["arguments"], verdict=d["verdict"],
                policy=d["policy"], reason=d["reason"], blocked=not d["forwarded"],
                result=_bounded(body, 1500),
            )
            # `results` is what the agent received; a proposal that never
            # reached the proxy produced no decision, so the two align.
            for d, body in zip(decisions, results, strict=True)
        ]
        return ChatReplyOut(said=said, calls=calls, truncated=transcript.truncated)
