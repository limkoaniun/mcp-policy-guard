"""The only module that knows Ollama's HTTP API exists.

Everything else takes the model as an injected callable, which is what lets the
loop be tested exhaustively with no daemon running.

On reproducibility, measured rather than assumed: at temperature 0,
`llama3.1:8b` returned byte-identical output across repeated runs AND across
different seeds -- decoding is greedy and the seed does nothing. `llama3.2` was
NOT identical even at a fixed seed; its first response after a load differed
from the two that followed, which agreed with each other. So `seed` is recorded
for completeness and must never be described as making a run reproducible, and
`warm_up` exists because that first-response difference is consistent with a
cold-start effect.
"""

from __future__ import annotations

import json
import urllib.request

from .transcript import ChatReply, ProposedCall

OLLAMA_URL = "http://localhost:11434"


def _post(url: str, body: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _get(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def _arguments(raw):
    """Read a call's arguments, or None if they cannot be read.

    ollama 0.33.2 returns a dict; older versions and some models return a JSON
    string. Anything else is left as None rather than guessed at -- the loop
    records that as a malformed call, which is a true statement about what the
    model produced.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def parse_reply(payload: dict) -> ChatReply:
    """Normalise one `/api/chat` response."""
    message = payload.get("message") or {}
    calls = []
    for entry in message.get("tool_calls") or []:
        function = entry.get("function") or {}
        calls.append(
            ProposedCall(
                name=function.get("name"),
                arguments=_arguments(function.get("arguments")),
                call_id=entry.get("id"),
                raw=entry,
            )
        )
    return ChatReply(
        content=message.get("content") or "",
        calls=calls,
        latency_ms=(payload.get("total_duration") or 0) / 1_000_000,
        raw_message=message,
    )


def model_fingerprint(model: str, *, url: str = OLLAMA_URL, timeout: int = 30) -> dict:
    """What produced a number: the daemon version and the model's digest.

    Recorded in the run manifest so a later reader can tell which weights a
    result came from.
    """
    version = _get(f"{url}/api/version", timeout).get("version", "unknown")
    tags = _get(f"{url}/api/tags", timeout).get("models") or []
    entry = next((m for m in tags if m.get("name") == model), {})
    return {
        "model": model,
        "digest": entry.get("digest", "unknown"),
        "size": entry.get("size"),
        "details": entry.get("details", {}),
        "ollama_version": version,
    }


def warm_up(model: str, *, url: str = OLLAMA_URL, timeout: int = 300) -> None:
    """Discard one request so the measured run is not the model's first.

    `llama3.2`'s first response after a load differed from those that followed.
    Whether that is a load effect or ordinary nondeterminism, a measured run
    should not be the one that is different for a reason unrelated to the
    experiment.
    """
    _post(
        f"{url}/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": "ready?"}],
            "stream": False,
            "options": {"temperature": 0.0},
        },
        timeout,
    )


def ollama_chat(
    model: str,
    *,
    url: str = OLLAMA_URL,
    temperature: float = 0.0,
    seed: int | None = None,
    timeout: int = 180,
):
    """A `Chat` callable bound to one model."""
    options: dict = {"temperature": temperature}
    if seed is not None:
        options["seed"] = seed

    def chat(messages: list[dict], tools: list[dict]) -> ChatReply:
        body = {"model": model, "messages": messages, "stream": False, "options": options}
        if tools:
            body["tools"] = tools
        return parse_reply(_post(f"{url}/api/chat", body, timeout))

    return chat
