"""The demonstration frontend's backend: one page and three JSON endpoints.

Spec §8's bounding rule -- "the frontend may only read existing audit-log and
evaluation artifacts" -- holds here. Every number this app serves comes from a
run directory the proxy wrote, via `demo.session.run_demo`. Nothing is
computed for display.

Each run gets a fresh directory under the system temp area, which is what
makes the reset button §8 calls "non-negotiable" unnecessary in this phase: a
run cannot start from a mutated record, because it never reuses one. A run
directory is single-use by design (see `state.RunDirectoryInUseError`).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..run_setup import off_arm_allowed
from ..scenarios_spec import load_all
from .session import run_demo

REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).parent / "static"
DEFAULT_PACK = REPO_ROOT / "configs" / "enrolment-personas.yaml"
DEFAULT_SCENARIOS = REPO_ROOT / "attacks" / "scenarios"
DEFAULT_PERSONAS = REPO_ROOT / "data" / "personas.json"

KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
}


class RunRequest(BaseModel):
    scenario_id: str
    enforcement: str


def create_app(
    *,
    repo_root: Path = REPO_ROOT,
    pack_path: Path = DEFAULT_PACK,
    scenarios_dir: Path = DEFAULT_SCENARIOS,
    personas_path: Path = DEFAULT_PERSONAS,
) -> FastAPI:
    personas = set(json.loads(personas_path.read_text(encoding="utf-8")))
    scenarios = {
        scenario.id: scenario
        for scenario in load_all(scenarios_dir, personas=personas, known_tools=KNOWN_TOOLS)
    }

    app = FastAPI(title="MCP Policy Guard — demonstration")

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

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
