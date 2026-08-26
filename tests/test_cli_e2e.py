"""End-to-end test of the shipped code path: `python -m policy_guard`.

Every other proxy test drives `PolicyProxy` directly, and the one MCP adapter
that *was* covered -- `EnforcementExtension.intercept_tool_call` -- has no
caller in the shipped path at all. So the suite verified an adapter the product
does not use, while `build_proxy_server` and `main()` went untested.

This runs the real CLI as a subprocess, over stdio, in front of the real
enrolment server it spawns for itself, and asserts the three properties the
slice claims: the tool list is mirrored unfiltered, an allowed call reaches
upstream, and a denied call is refused without reaching it. It also asserts the
end-of-run marker, which only a clean shutdown of the real `main()` writes.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from policy_guard.__main__ import EXIT_RUN_FAILED
from policy_guard.upstream import connect_subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]

# S1000001 is enrolled in 37014 in the committed dataset. The task below is
# read-only, so `drop` is not declared and the allowlist policy denies it --
# the same shape as the output-injection scenario, minus the model.
STUDENT = "S1000001"
ENROLLED = "37014"

TASK = f"""
task_id: CLI-E2E
description: Read-only lookup task; `drop` is deliberately not declared.
allowed_tools: [get_subject_details, get_student_record]
principal_id: {STUDENT}
"""


def cli_arguments(task_path, run_dir) -> list[str]:
    return [
        "-m",
        "policy_guard",
        "--task",
        str(task_path),
        "--policy-pack",
        str(REPO / "configs" / "enrolment.yaml"),
        "--run-dir",
        str(run_dir),
        "--enforcement",
        "on",
    ]


@pytest.mark.anyio
async def test_the_shipped_cli_mirrors_allows_and_denies(tmp_path):
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK)
    run_dir = tmp_path / "run"

    async with connect_subprocess(
        sys.executable, cli_arguments(task_path, run_dir), cwd=str(REPO)
    ) as client:
        # 1. the tool list is mirrored unfiltered -- `drop` is on the menu even
        #    though this task may not call it, so an attack that fails fails
        #    because the proxy refused it, not because the tool was hidden.
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {
            "search_subjects",
            "get_subject_details",
            "get_student_record",
            "check_prerequisites",
            "enrol",
            "drop",
            "submit_erequest",
        }

        # 2. an allowed call reaches the upstream and returns its real answer
        allowed = await client.call_tool("get_student_record", {"student_id": STUDENT})
        assert allowed.is_error is False
        assert ENROLLED in str(allowed.content) + str(allowed.structured_content)

        # 3. a denied call is refused, with the reason, and never runs
        denied = await client.call_tool(
            "drop", {"student_id": STUDENT, "code": ENROLLED}
        )
        assert denied.is_error is True
        assert "drop" in str(denied.content)

    # ground truth: the upstream's own state file still holds the enrolment
    state = json.loads((run_dir / "state.json").read_text())
    assert state[STUDENT]["enrolled"] == [ENROLLED], "the drop must not have happened"

    records = [
        json.loads(line)
        for line in (run_dir / "decisions.jsonl").read_text().splitlines()
        if line.strip()
    ]
    by_tool = {record["tool"]: record for record in records}
    assert by_tool["get_student_record"]["verdict"] == "allow"
    assert by_tool["get_student_record"]["forwarded"] is True
    assert by_tool["drop"]["verdict"] == "deny"
    assert by_tool["drop"]["forwarded"] is False


@pytest.mark.anyio
async def test_a_clean_run_writes_the_end_of_run_marker(tmp_path):
    """`state.json` says the run started; `complete` says it finished.

    Without the marker a run killed partway through leaves artifacts that a
    harness cannot tell from a run in which the defence held.
    """
    task_path = tmp_path / "task.yaml"
    task_path.write_text(TASK)
    run_dir = tmp_path / "run"

    async with connect_subprocess(
        sys.executable, cli_arguments(task_path, run_dir), cwd=str(REPO)
    ) as client:
        await client.call_tool("get_student_record", {"student_id": STUDENT})
        # the marker is written at shutdown, not at startup
        assert not (run_dir / "complete").exists()

    assert (run_dir / "complete").exists(), (
        "a clean shutdown of `python -m policy_guard` must mark the run complete"
    )


DYING_PACK = """
name: fake
upstream_command: python
upstream_args: [{script}, --die-on-call]
policies: [allowlist]
trust: {{}}
"""

DYING_TASK = """
task_id: CLI-DEAD-UPSTREAM
description: One allowed call against an upstream that dies when called.
allowed_tools: [read_thing]
"""


def test_an_upstream_dying_mid_task_fails_the_run_loudly(tmp_path):
    """Spec 10: an upstream that dies mid-task must fail the run loudly.

    Before the fix the CLI ran its proxy server with the SDK default
    `raise_exceptions=False`, so a handler whose `client.call_tool` failed
    against a dead subprocess became an ordinary error result. The agent saw a
    tool failure and carried on; every later call was still logged
    `forwarded: true` for a call that reached nothing; `state.json` froze at
    whatever it held; and a harness reading those artifacts scored the frozen
    state as an attack that had been blocked.

    Two things had to change, and this test needs both. `raise_exceptions=True`
    alone is not enough: the SDK's request loop maps an `MCPError` -- which is
    exactly what a dead session raises -- to a wire error *before* it consults
    that flag, so the run stayed quiet and still marked itself complete.
    `PolicyProxy.call` therefore re-raises transport failures as `UpstreamError`,
    which has no wire mapping and so actually escapes.

    Driven with raw JSON-RPC rather than an MCP client because a client waits
    on a reply the dying proxy never sends; here the assertion is on the CLI
    process itself, which is what "loudly" has to mean.
    """
    script = REPO / "tests" / "fake_upstream.py"
    pack_path = tmp_path / "pack.yaml"
    pack_path.write_text(DYING_PACK.format(script=script))
    task_path = tmp_path / "task.yaml"
    task_path.write_text(DYING_TASK)
    run_dir = tmp_path / "run"

    messages = [
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "read_thing", "arguments": {"code": "X"}},
        },
    ]

    process = subprocess.Popen(
        [
            sys.executable, "-m", "policy_guard",
            "--task", str(task_path),
            "--policy-pack", str(pack_path),
            "--run-dir", str(run_dir),
            "--enforcement", "on",
        ],
        cwd=str(REPO),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # stdin is deliberately left open. Closing it is an ordinary peer
        # disconnect and therefore a clean shutdown; the run must fail on the
        # dead upstream alone, with the connection still live.
        process.stdin.write("".join(json.dumps(m) + "\n" for m in messages))
        process.stdin.flush()
        returncode = process.wait(timeout=60)
        stderr = process.stderr.read()
    finally:
        if process.poll() is None:  # pragma: no cover - only on a hang
            process.kill()
            process.wait(timeout=10)

    assert returncode == EXIT_RUN_FAILED, (
        f"the proxy exited {returncode}, not {EXIT_RUN_FAILED}, after its "
        "upstream died mid-task"
    )
    assert "UpstreamError" in stderr
    assert "must be excluded from scoring" in stderr
    assert not (run_dir / "complete").exists(), (
        "a run whose upstream died mid-task must not be marked complete -- "
        "the harness would otherwise score its frozen state as a blocked attack"
    )
