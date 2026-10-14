"""The demonstration frontend's backend: one page and the JSON endpoints the
chat page uses.

Spec §8's bounding rule -- "the frontend may only read existing audit-log and
evaluation artifacts" -- holds here. Every number this app serves comes from a
run directory the proxy wrote, via `demo.session.run_demo`. Nothing is
computed for display.

Each run gets a fresh directory under the system temp area, which is what
makes the reset button §8 calls "non-negotiable" unnecessary in this phase: a
run cannot start from a mutated record, because it never reuses one. A run
directory is single-use by design (see `state.RunDirectoryInUseError`).

The attack arrives the way the pivot note says it does: inside a document the
presenter drags onto the chat, read through `get_attachment`. The shipped
documents under `data/documents/` are the scenario texts as PDFs, the
planted footer white and tiny.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent.ollama import installed_models, ollama_chat
from ..run_setup import off_arm_allowed
from ..scenarios_spec import load_all
from .chat import ChatSession
from .documents import documents_from
from .session import run_demo
from .uploads import MAX_BYTES, UploadError, attachment_id_for, extract_text, match_known, safe_name

REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).parent / "static"
DEFAULT_PACK = REPO_ROOT / "configs" / "enrolment-personas.yaml"
#: The suite of record and the reply-channel subset. Both load into the demo
#: so every shipped document, A-01..A-13, can be dropped on the chat; the
#: demo set is loaded too, so its three PDFs under `data/documents/demo/` are
#: recognised on upload. The campaigns keep their own `--scenarios` and are
#: not affected.
DEFAULT_SCENARIO_DIRS = (
    REPO_ROOT / "attacks" / "scenarios",
    REPO_ROOT / "attacks" / "scenarios-reply",
    REPO_ROOT / "attacks" / "scenarios-demo",
)
DEFAULT_PERSONAS = REPO_ROOT / "data" / "personas.json"

KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
    "get_attachment",
}


#: The tools the enrolment server exposes, in the order the page lists them,
#: with the side-effectful ones marked. The page draws its checkboxes from
#: this rather than from a list of its own, so a tool added upstream cannot
#: go missing from the console.
TOOLS = [
    {"name": "search_subjects", "side_effect": False},
    {"name": "get_subject_details", "side_effect": False},
    {"name": "get_student_record", "side_effect": False},
    {"name": "check_prerequisites", "side_effect": False},
    {"name": "get_study_plan", "side_effect": False},
    {"name": "get_session_options", "side_effect": False},
    {"name": "preview_major_change", "side_effect": False},
    {"name": "get_attachment", "side_effect": False},
    {"name": "enrol", "side_effect": True},
    {"name": "drop", "side_effect": True},
    {"name": "submit_erequest", "side_effect": True},
]

READ_ONLY = [t["name"] for t in TOOLS if not t["side_effect"]]

#: Presets behind the console's three task buttons.
PRESETS = {
    "read-only": READ_ONLY,
    "enrolment": [*READ_ONLY, "enrol", "submit_erequest"],
    "everything": [t["name"] for t in TOOLS],
}


class NewSession(BaseModel):
    persona: str = "S9000001"
    preset: str = "enrolment"
    mode: Literal["rag", "agent-on", "agent-off"] = "agent-on"


class ModeUpdate(BaseModel):
    mode: Literal["rag", "agent-on", "agent-off"]


class ToolsUpdate(BaseModel):
    allowed_tools: list[str]


class EnforcementUpdate(BaseModel):
    enforcement: str


class Message(BaseModel):
    text: str
    model: str | None = None


class RunRequest(BaseModel):
    scenario_id: str
    enforcement: str
    #: Omitted for a scripted replay; a model name drives the run live, and
    #: the calls become the model's own rather than the scenario's script.
    model: str | None = None


class Approval(BaseModel):
    turn: int
    index: int


def create_app(
    *,
    repo_root: Path = REPO_ROOT,
    pack_path: Path = DEFAULT_PACK,
    scenarios_dirs: Sequence[Path] = DEFAULT_SCENARIO_DIRS,
    personas_path: Path = DEFAULT_PERSONAS,
    chat_factory=None,
    available_models=None,
) -> FastAPI:
    # Injectable for the reason the campaign runners make them so: the app is
    # then testable with no daemon running, and the tests assert on what the
    # harness did rather than on what a model happened to say.
    chat_factory = chat_factory or (lambda model: ollama_chat(model, temperature=0.0, num_ctx=16384))
    available_models = available_models or installed_models
    persona_records = json.loads(personas_path.read_text(encoding="utf-8"))
    personas = set(persona_records)
    # Major titles from the built catalogue, so the picker shows "Cybersecurity"
    # rather than a MAJ code nobody watching can decode.
    aos = json.loads((repo_root / "data" / "aos.json").read_text(encoding="utf-8"))
    scenarios = {
        scenario.id: scenario
        for root in scenarios_dirs
        for scenario in load_all(root, personas=personas, known_tools=KNOWN_TOOLS)
    }
    documents = documents_from(scenarios.values())

    app = FastAPI(title="MCP Policy Guard — demonstration")

    # The built React bundle. `frontend/` is the source; its output is
    # committed here so the demo runs from a clone with no node toolchain --
    # a viva is not the place to discover `npm install` is a prerequisite.
    assets = STATIC / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def page() -> FileResponse:
        # No-store, because `FileResponse` otherwise sends an ETag and the
        # browser holds the old page across an edit. During a demo that shows
        # a stale UI against a live backend, which is the most confusing
        # failure this app can have: the numbers are right and the page is
        # not.
        return FileResponse(
            STATIC / "index.html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    @app.get("/api/personas")
    def list_personas() -> list[dict]:
        """The students the picker offers, each with the record's shape.

        Built from the same personas file the sessions run on, so a student
        offered here is by construction one a session can start as.

        The name is display-only: `Student` drops it, so no tool ever returns it.
        """
        return [
            {
                "id": student_id,
                "major": aos.get(record.get("major"), {}).get("title", record.get("major")),
                "completed": len(record.get("completed", [])),
                "enrolled": len(record.get("enrolled", [])),
                "name": record.get("name"),
            }
            for student_id, record in sorted(persona_records.items())
        ]

    @app.get("/api/scenarios")
    def list_scenarios() -> list[dict]:
        """Every scenario, with whether it can be shown on both arms.

        The undeliverable ones are listed rather than hidden: four of the
        eight injected scenarios have no payload-delivery mechanism, and a
        picker that silently omitted them would misrepresent the suite's
        coverage. `has_off_arm` is what the page uses to disable the toggle.
        """
        return [
            {
                "id": scenario.id,
                "persona": scenario.persona,
                "attack_class": scenario.attack_class,
                "task": scenario.task.description,
                "has_off_arm": off_arm_allowed(scenario),
            }
            for scenario in sorted(scenarios.values(), key=lambda s: s.id)
        ]

    @app.get("/api/documents")
    def list_documents() -> list[dict]:
        """The known-document texts `match_known` compares an upload against,
        so the upload route can tag it with its scenario's own prompt.

        Nothing for the page: there is no attach control left to offer these
        from, since a student now drags a file onto the chat instead.
        """
        return [document.model_dump() for document in documents]

    sessions: dict[str, ChatSession] = {}
    session_root = Path(tempfile.mkdtemp(prefix="demo-chat-"))

    def state_of(live: ChatSession) -> dict:
        return {
            "record": live.record(),
            "allowed_tools": live.allowed_tools,
            "enforcement": live.enforcement,
            "mode": live.mode,
            "attachments": [
                {"id": attachment_id, **entry} for attachment_id, entry in live.attachments.items()
            ],
            "erequests": live.erequests,
        }

    def get(session_id: str) -> ChatSession:
        live = sessions.get(session_id)
        if live is None:
            raise HTTPException(404, f"no session {session_id!r}; it may have expired")
        return live

    @app.get("/api/tools")
    def tools() -> dict:
        return {"tools": TOOLS, "presets": PRESETS}

    @app.post("/api/chat")
    def new_session(request: NewSession) -> dict:
        if request.persona not in personas:
            # A session for an unknown student would not fail here; it would
            # fail later, mid-demonstration, when the record lookup misses.
            # Refusing now keeps the failure at the caller with a reason.
            raise HTTPException(400, f"no persona {request.persona!r}")
        session_id = uuid.uuid4().hex[:12]
        sessions[session_id] = ChatSession(
            persona=request.persona,
            root=session_root / session_id,
            pack_path=pack_path,
            repo_root=repo_root,
            allowed_tools=PRESETS.get(request.preset, PRESETS["enrolment"]),
        )
        sessions[session_id].set_mode(request.mode)
        return {"id": session_id, **state_of(sessions[session_id])}

    @app.put("/api/chat/{session_id}/mode")
    def set_mode(session_id: str, update: ModeUpdate) -> dict:
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is running; wait before switching modes")
        live.set_mode(update.mode)
        return state_of(live)

    @app.put("/api/chat/{session_id}/tools")
    def set_tools(session_id: str, update: ToolsUpdate) -> dict:
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is running")
        known = {t["name"] for t in TOOLS}
        unknown = [t for t in update.allowed_tools if t not in known]
        if unknown:
            raise HTTPException(400, f"no such tool: {', '.join(unknown)}")
        live.allowed_tools = list(update.allowed_tools)
        return state_of(live)

    @app.put("/api/chat/{session_id}/enforcement")
    def set_enforcement(session_id: str, update: EnforcementUpdate) -> dict:
        if update.enforcement not in ("on", "off"):
            raise HTTPException(400, "enforcement must be 'on' or 'off'")
        live = get(session_id)
        if live.busy or live.rag_mode:
            raise HTTPException(409, "use the mode selector when idle to change modes")
        live.enforcement = update.enforcement
        return state_of(live)

    @app.post("/api/chat/{session_id}/upload")
    async def upload(
        session_id: str, file: UploadFile = File(...),  # noqa: B008 - FastAPI's own idiom
    ) -> dict:
        """A document dragged onto the chat becomes one of the session's
        attachments, served by `get_attachment` on every later message.

        The text is what pypdf pulls out of the file; the file itself is kept
        under the session's `uploads/` as evidence and never read back. If
        the text matches one of the shipped documents, the entry says which
        and in which variant, so the page can offer the scenario's own
        request and mark the footer. That match is checked against all four
        shipped documents regardless of the session's persona, so Jane's
        document dropped into Tom's session is attached and tagged with
        Jane's prompt too -- any file is accepted now, and the tag only says
        what the file is, not who it belongs to. A session mid-message
        refuses the upload: the running proxy already has its
        `attachments.json`.
        """
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is running; attach the file when it finishes")
        data = await file.read(MAX_BYTES + 1)
        if live.busy:
            raise HTTPException(409, "a message is running; retry the upload afterwards")
        try:
            name = safe_name(file.filename)
        except UploadError as error:
            raise HTTPException(400, str(error)) from error
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{name} is over the 2 MB limit")
        try:
            kind, text = extract_text(name, data)
        except UploadError as error:
            raise HTTPException(400, str(error)) from error
        attachment_id = attachment_id_for(name)
        kept = live.root / "uploads" / attachment_id / name
        if len(str(kept)) > 240:
            # Keep the displayed name in session metadata; avoid MAX_PATH on
            # Windows for the retained evidence copy of a long upload name.
            key = hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
            kept = live.root / "uploads" / key / ("original" + Path(name).suffix)
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
        live.attachments[attachment_id] = {
            "name": name, "text": text, "kind": kind, "known": match_known(text, name, documents),
        }
        return state_of(live)

    @app.delete("/api/chat/{session_id}/attachments/{attachment_id}")
    def detach(session_id: str, attachment_id: str) -> dict:
        """The chip's ×. The kept file stays on disk, like turn directories."""
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is running")
        if attachment_id not in live.attachments:
            raise HTTPException(404, f"no attachment {attachment_id!r}")
        del live.attachments[attachment_id]
        return state_of(live)

    @app.post("/api/chat/{session_id}/reset")
    def reset(session_id: str) -> dict:
        """Put the record back and forget the conversation, keeping the
        console as it was staged. See `ChatSession.reset`."""
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is running")
        live.reset()
        return state_of(live)

    @app.post("/api/chat/{session_id}/approve")
    async def approve(session_id: str, body: Approval) -> dict:
        """The approval card's Allow. Refuse has no endpoint: nothing ran, so
        there is nothing to log, and the page says so on its own."""
        live = get(session_id)
        if live.rag_mode:
            raise HTTPException(409, "RAG has no actions to approve")
        try:
            confirmation = await live.approve(turn=body.turn, index=body.index)
        except (LookupError, ValueError) as error:
            raise HTTPException(400, str(error)) from error
        return {**state_of(live), "confirmation": confirmation.model_dump()}

    @app.post("/api/chat/{session_id}/message")
    async def message(session_id: str, body: Message) -> StreamingResponse:
        """Stream the run as it happens.

        The loop's `on_event` is a synchronous callback and the response is an
        async generator, so a queue bridges them: the callback puts, the
        generator yields. Without this the page would receive one blob after
        the model finished, which for a thirty-second call is a spinner and
        nothing else.
        """
        live = get(session_id)
        if live.busy:
            raise HTTPException(409, "a message is already running")
        if not body.text.strip():
            raise HTTPException(400, "message text must not be empty")
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()

        def on_event(event):
            if threading.get_ident() == loop_thread:
                queue.put_nowait(event)
            else:
                loop.call_soon_threadsafe(queue.put_nowait, event)

        async def drive():
            try:
                reply = await live.send(
                    body.text,
                    chat=chat_factory(body.model) if body.model else chat_factory(None),
                    model=body.model or "scripted",
                    on_event=on_event,
                )
                await queue.put({
                    "kind": "done",
                    "said": reply.said,
                    "calls": [c.model_dump() for c in reply.calls],
                    "truncated": reply.truncated,
                    "turn": reply.turn,
                    "context": reply.context,
                    "model": body.model or "scripted",
                    "latency_ms": reply.latency_ms,
                    "clarification": reply.clarification,
                    **state_of(live),
                })
            except Exception as error:  # noqa: BLE001 - the page must be told
                await queue.put({"kind": "error", "detail": str(error)})
            finally:
                await queue.put(None)

        async def stream():
            task = asyncio.create_task(drive())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                await task

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})

    @app.get("/api/models")
    def models() -> list[str]:
        """What Ollama actually holds. An empty list means the daemon is not
        running, and the page says so rather than offering a picker that
        cannot work."""
        try:
            return available_models()
        except Exception:  # noqa: BLE001 - a missing daemon is a normal state here
            return []

    @app.post("/api/run")
    async def run(request: RunRequest) -> dict:
        scenario = scenarios.get(request.scenario_id)
        if scenario is None:
            raise HTTPException(404, f"no scenario {request.scenario_id!r}")
        if request.enforcement not in ("on", "off"):
            raise HTTPException(400, "enforcement must be 'on' or 'off'")

        with tempfile.TemporaryDirectory(prefix="demo-run-") as tmp:
            try:
                result = await run_demo(
                    scenario,
                    enforcement=request.enforcement,
                    run_dir=Path(tmp) / "run",
                    pack_path=pack_path,
                    repo_root=repo_root,
                    chat=chat_factory(request.model) if request.model else None,
                    model=request.model,
                )
            except ValueError as error:
                # `run_demo` refuses an off arm for an undeliverable scenario
                # and a persona absent from the pack. Both are the caller
                # asking for something the suite cannot honestly show, so
                # both are 400s carrying the module's own explanation rather
                # than a generic failure.
                raise HTTPException(400, str(error)) from error
        return result.model_dump()

    return app
