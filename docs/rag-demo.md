# RAG chat integration — 2026-09-30

The demo now has three modes: RAG chatbot, Agent without guard, and Agent
with guard. The existing batch advice runner and saved experimental results
are unchanged. RAG uses the existing deterministic course-code / English
name-keyword retriever, not embeddings or a vector database.

## Run locally on Windows

From the project directory:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-demo.ps1
```

Open http://127.0.0.1:8765. The script uses the project's `.venv`, starts the
portable Ollama in `.local/ollama` if needed, and stores model weights in
`.local/models`. These machine-local files are ignored by Git. To stop the
web server, press Ctrl+C. Ollama remains available in the background.

For a fresh checkout, install Python 3.11+, create `.venv`, install
`pip install -e ".[dev]"`, and install Ollama from https://ollama.com/download/windows.
Run `ollama pull qwen2.5:7b` with that Ollama installation before opening the
demo. An already running standard Ollama installation also works with the script.

## What to demonstrate

1. Select a student, model, and **RAG chatbot**.
2. Ask `Tell me about 32513`, then `Am I eligible for it?`.
3. Ask `What can I take next session?`, then reply `Spring`.
4. Upload a PDF or TXT and ask about its contents. Expand **Retrieved
   context** to see exactly the fresh material supplied on that turn.
5. Ask RAG to enrol or submit a request. It has no tools; the record and
   eRequest log cannot change, regardless of what the model says.
6. Switch to either Agent mode and repeat using the same model and attachment.

Changing mode clears conversation history, pending approvals, and eRequests,
and restores the student's baseline record. Attachments and tool selections
remain. Switching student creates a separate session without the old uploads.
Start over resets the record and history while retaining the selected mode.

## API and evidence

- `POST /api/chat` accepts `mode`: `rag`, `agent-off`, or `agent-on`.
- `PUT /api/chat/{id}/mode` changes mode with the reset semantics above.
- The existing message endpoint returns SSE `said` then `done`; RAG `done`
  includes `context`, `model`, `latency_ms`, and `clarification`, with no calls.
- `%TEMP%/demo-chat-*/{id}/turn-NNN/rag.json` stores the exact model messages,
  fresh retrieved blocks, retrieval query, reply, session, and model latency.
- Clarifications are labelled and saved with zero latency and no model call.
- The legacy enforcement endpoint remains compatible for existing Agent
  clients; the new UI uses the mode endpoint.

The model receives the student's own raw record and published rules, not
the rule evaluator's answer. Uploaded text remains untrusted data, and is
included without the demo's clean/planted tags. Recent user/assistant dialogue
is supplied for follow-ups; explicit subject references are resolved from user
messages. Removed attachments are absent from fresh retrieval, although prior
answers may still mention them; start over to clear the dialogue too.

## Limits and experiment boundary

- Session selection is deterministic: explicit Autumn/Spring/Summer (also
  春季/秋季/夏季) or a clarification, not a guessed academic calendar.
- Demo prompts exceeding 24,000 characters are rejected before a model call;
  this is a conservative size guard, not an exact token count. The demo
  explicitly requests a 16,384-token Ollama context window. Large documents
  need narrowing; no silent truncation or chunking is added.
- RAG can produce incorrect or injected text even though it cannot execute
  actions. The absence of state mutations is an architectural property.
- The new conversation behavior is demo-only. Existing single-turn batch
  advice prompts and scoring denominators have not been replaced or rerun.
- Session-long taint on the Agent arm remains separate unfinished work.

## Verification

```powershell
$env:PYTHONUTF8 = '1'
$env:PATH = (Join-Path $PWD '.venv/Scripts') + ';' + $env:PATH
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/enrolment/rag.py src/enrolment/demo tests/test_demo_rag.py
```

New API tests cover unchanged records, PDF context, follow-ups, session
clarification, mode/student isolation, context overflow, provider failure,
unexpected model tool proposals, and mutations rejected during a running call.

Windows needs the UTF-8 setting for older tests that read the dataset using
the platform's default encoding, and the virtual environment on PATH for
fixture packs whose upstream command is `python`.
