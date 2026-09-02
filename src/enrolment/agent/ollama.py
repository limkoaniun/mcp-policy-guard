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
import time
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


def _provider_latency_ms(payload: dict) -> float | None:
    """Ollama's self-reported `total_duration`, in milliseconds, or `None`.

    This is a *provider*-reported figure, not the wall-clock latency the
    design's §6/§7 name and build the overhead metric on: `total_duration`
    excludes HTTP transport time, which the wall-clock figure -- measured
    around the request in `ollama_chat` -- does not. `None` here means the
    field was absent from the payload, which `(payload.get(...) or 0)` would
    otherwise silently render as a confident zero milliseconds -- a
    measurement that did not happen, reported as though it did.
    """
    total_duration = payload.get("total_duration")
    if total_duration is None:
        return None
    return total_duration / 1_000_000


def parse_reply(payload: dict) -> ChatReply:
    """Normalise one `/api/chat` response.

    A provider can send a shape this harness has never seen -- a string where
    a list belongs, a dict where an object belongs. This is experimental data
    collection, not a config check at startup: a run that raises on one bad
    response loses every turn already recorded, while a run that records "the
    provider sent something unreadable" keeps the rest of the evidence and
    still says something true. So every level here is guarded rather than
    trusted, and an unreadable shape becomes a `ProposedCall` with
    `name=None, arguments=None` -- which the loop records as malformed --
    rather than a crash. The raw payload is always kept, however unreadable,
    so a later reader can see what the provider actually sent.

    Does not set `ChatReply.latency_ms` -- this function only normalises an
    already-received payload and has no idea how long the request took.
    `ollama_chat` measures wall clock around the request and sets it there;
    a `ChatReply` from `parse_reply` alone keeps the default 0.0, meaning
    "not measured here", not "instant." Ollama's own self-reported duration,
    when present, is carried separately as `provider_latency_ms`.
    """
    message = payload.get("message")
    if not isinstance(message, dict):
        message = {}

    raw_calls = message.get("tool_calls")
    entries = raw_calls if isinstance(raw_calls, list) else []

    calls = []
    for entry in entries:
        if not isinstance(entry, dict):
            calls.append(ProposedCall(name=None, arguments=None, raw={"unparsed": entry}))
            continue
        function = entry.get("function")
        if not isinstance(function, dict):
            calls.append(
                ProposedCall(name=None, arguments=None, call_id=entry.get("id"), raw=entry)
            )
            continue
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
        provider_latency_ms=_provider_latency_ms(payload),
        raw_message=message,
    )


def model_fingerprint(model: str, *, url: str = OLLAMA_URL, timeout: int = 30) -> dict:
    """What produced a number: the daemon version and the model's digest.

    Recorded in the run manifest so a later reader can tell which weights a
    result came from.

    A bare name matches its `:latest` tag. `ollama pull llama3.2` puts the
    weights on the machine and the daemon then lists them as `llama3.2:latest`
    -- never under the bare name -- so an exact-string match reported the
    model as absent when it was present, and the campaign preflight that gates
    on this digest refused to start over it. `digest: "unknown"` is this
    module's way of saying "the daemon does not have this", and it must mean
    only that.
    """
    version = _get(f"{url}/api/version", timeout).get("version", "unknown")
    tags = _get(f"{url}/api/tags", timeout).get("models") or []
    entry = next(
        (m for m in tags if m.get("name") in (model, f"{model}:latest")), {}
    )
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
    """A `Chat` callable bound to one model.

    `latency_ms` on the returned reply is wall clock measured around the
    HTTP request here -- the "wall-clock latency of the model call" the
    design's §6 names -- not Ollama's self-reported `total_duration`, which
    excludes transport time and is kept separately as `provider_latency_ms`.
    """
    options: dict = {"temperature": temperature}
    if seed is not None:
        options["seed"] = seed

    def chat(messages: list[dict], tools: list[dict]) -> ChatReply:
        body = {"model": model, "messages": messages, "stream": False, "options": options}
        if tools:
            body["tools"] = tools
        started = time.perf_counter()
        payload = _post(f"{url}/api/chat", body, timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        reply = parse_reply(payload)
        reply.latency_ms = elapsed_ms
        return reply

    return chat
