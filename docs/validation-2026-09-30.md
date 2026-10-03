# Local validation — 2026-09-30

## Environment

Windows, Python 3.12.14 in `.venv`, MCP / mcp-types 2.2.0, Ollama 0.35.0,
qwen2.5:7b, RTX 5070 (11.9 GiB VRAM), 32 GB system RAM. Ollama is bound to
127.0.0.1:11434; the demo is bound to 127.0.0.1:8765. The runtime and weights
are project-local under `.local/`, excluded from the source distribution.

Official runtime documentation: https://docs.ollama.com/windows.

## Automated checks

- New RAG API tests: **12 passed**.
- Final focused regression: `test_enrolment_server.py`, `test_proxy.py`,
  `test_demo_rag.py`: **58 passed**.
- Additional final regression: existing demo chat/API, RAG core, advice CLI,
  and advice scoring tests: **95 passed**. Together these two final runs
  cover 153 distinct tests, including the 12 new RAG API tests.
- Existing RAG / advice CLI / advice scoring checks: **49 passed**.
- Changed Python modules and tests pass Ruff; Vite production build passes.
- Broad run excluding `test_cli_e2e.py`: **925 passed, 4 failed, 6 skipped**
  before compatibility repairs. The four failures were MCP 2.2's new error
  masking, and all four pass in the final focused regression above. This
  is not reported as a fresh all-green full-suite run.
- Separately, `test_cli_e2e.py`: the first **6 passed**, then
  `test_an_upstream_dying_mid_task_fails_the_run_loudly` timed out after
  60 seconds; `-x` stopped the remaining three. This pre-existing Windows
  abnormal-process-exit behavior remains open. Normal proxy calls and
  attachment taint end-to-end tests passed.
- Repository-wide Ruff still reports three pre-existing findings in
  `scenarios_spec.py`, `policy_guard/config.py`, and `tests/test_taint.py`.

Windows startup uses UTF-8 and the virtual environment on PATH. A pre-existing
long-name upload failure was repaired by using a compact retained-evidence
path while preserving the original display name. Expected attachment and
eRequest validation errors now use the SDK's `ToolError`, preserving useful
messages without globally exposing unexpected exceptions. The proxy test
checks that the upstream's actual error crosses the proxy unchanged, since
MCP 2.2 masks the contents of unexpected exceptions.

## Real model smoke run

Seven turns completed through the HTTP API:

1. RAG course query for 32513: two retrieved blocks, no calls.
2. RAG follow-up eligibility question: prior subject retrieved, no calls.
3. Ambiguous next-session request: explicit clarification, no model call.
4. Spring recommendation: course structure supplied, no calls.
5. Uploaded clean RPL PDF summary: extracted document text supplied, no calls.
6. Agent without guard: real `get_subject_details` call and final answer.
7. Agent with guard: real `get_subject_details` call and final answer.

All RAG turns left the student record and eRequest log unchanged. The first
cold model request took about 43 seconds; subsequent RAG requests took about
3–4 seconds. The browser also displayed a real RAG answer and its two context
blocks, with a measured 2.56-second request. These are smoke timings, not a
performance benchmark.

**A measured quality limitation:** the model incorrectly answered that Jane
(S9000001) was eligible for 32513. The deterministic evaluator says she is not,
with 32130 missing. This was retained as a model error, not corrected by feeding
the evaluator's answer into RAG. Passing the smoke run means the integration
works, not that its advice is always correct.

Machine-local evidence: `.local/live-validation.json`, `.local/tests.log`,
`.local/tests.xml`, `.local/cli-tests.log`, and `.local/python-environment.txt`.
The final 95-test run is recorded in `.local/final-focused.log`.
Historical scored campaigns were not modified or rerun.
