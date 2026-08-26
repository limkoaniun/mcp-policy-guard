"""Polite, cached HTTP fetching for the enrolment dataset.

Every response is written to disk with its URL and timestamp, and the cache is
committed to the repository, so experiments never depend on a live UTS server
and the dataset's provenance stays auditable.

Requests are serial with a delay between them. `coursehandbook.uts.edu.au`
publishes a blanket `Disallow: /` in robots.txt; the fetch is deliberately
bounded to one course and run once. See the design spec for the access
question that should be confirmed with the supervisor.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

USER_AGENT = "mcp-policy-guard research (UTS 32933)"


@dataclass(frozen=True)
class FetchResult:
    url: str
    name: str
    body: str
    fetched_at: str
    from_cache: bool


def _urllib_transport(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "replace")


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        delay: float = 2.0,
        transport: Callable[[str], str] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.delay = delay
        self.transport = transport or _urllib_transport

    def get(self, url: str, name: str) -> FetchResult:
        body_path = self.cache_dir / name
        meta_path = self.cache_dir / f"{name}.meta.json"

        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return FetchResult(
                url=meta["url"],
                name=name,
                body=body_path.read_text(encoding="utf-8"),
                fetched_at=meta["fetched_at"],
                from_cache=True,
            )

        body = self.transport(url)
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body_path.write_text(body, encoding="utf-8")
        meta_path.write_text(
            json.dumps({"url": url, "fetched_at": fetched_at}, indent=1), encoding="utf-8"
        )
        if self.delay:
            time.sleep(self.delay)
        return FetchResult(
            url=url, name=name, body=body, fetched_at=fetched_at, from_cache=False
        )
