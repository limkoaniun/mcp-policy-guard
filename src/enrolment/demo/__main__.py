"""Run one scenario on both arms and print the comparison as JSON.

Phase 1 of the brief: no streaming, no browser. This is what proves the path
end to end, and what a reader can run to see the difference for themselves:

    python -m enrolment.demo \
        --scenario attacks/scenarios/output-injection/SC-06.yaml \
        --run-dir experiments/runs/demo/sc-06

The default pack is the personas one, and not for convenience: the general
pack's dataset holds no scenario personas, and running there makes both arms
agree for a reason that has nothing to do with enforcement. See the brief's
§6.2. `run_demo` refuses that case outright; defaulting correctly means
nobody has to meet the refusal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..scenarios_spec import load_scenario
from .session import run_demo

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PACK = "configs/enrolment-personas.yaml"
DEFAULT_PERSONAS = "data/personas.json"

KNOWN_TOOLS = {
    "search_subjects", "get_subject_details", "get_student_record",
    "check_prerequisites", "enrol", "drop", "submit_erequest",
    "get_study_plan", "preview_major_change", "get_session_options",
}


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one scenario on both enforcement arms and print the comparison."
    )
    parser.add_argument("--scenario", required=True, help="path to a scenario YAML")
    parser.add_argument("--run-dir", required=True, help="where both arms' runs are written")
    parser.add_argument("--pack", default=DEFAULT_PACK)
    parser.add_argument("--personas", default=DEFAULT_PERSONAS)
    return parser.parse_args(argv)


async def _both_arms(scenario, *, run_dir: Path, pack_path: Path) -> dict:
    arms = {}
    for enforcement in ("on", "off"):
        result = await run_demo(
            scenario,
            enforcement=enforcement,
            run_dir=run_dir / enforcement,
            pack_path=pack_path,
            repo_root=REPO_ROOT,
        )
        arms[enforcement] = result.model_dump()
    return arms


def main(argv=None) -> int:
    args = parse_args(argv)
    personas = set(json.loads((REPO_ROOT / args.personas).read_text(encoding="utf-8")))
    scenario = load_scenario(args.scenario, personas=personas, known_tools=KNOWN_TOOLS)
    arms = asyncio.run(
        _both_arms(scenario, run_dir=Path(args.run_dir), pack_path=REPO_ROOT / args.pack)
    )
    print(json.dumps(arms, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
