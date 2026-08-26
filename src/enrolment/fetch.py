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


class FetchDecodeError(ValueError):
    """Raised when a response body does not decode as its declared charset."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    name: str
    body: str
    fetched_at: str
    from_cache: bool
    status: int | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class TransportResponse:
    """What a transport hands back: the decoded body plus how it arrived."""

    body: str
    status: int | None = None
    content_type: str | None = None


def _urllib_transport(url: str) -> TransportResponse:
    """Fetch one URL, decoding with the charset the server actually declared.

    Decoding as UTF-8 with errors="replace" was silently destructive: a page
    served as windows-1252 would turn the apostrophe in "Master's Coursework
    courses owned by FEIT" into U+FFFD, and because the body is written
    straight to the committed cache, offline re-parsing could never recover
    it. errors="strict" makes a charset mismatch fail loudly instead of
    poisoning the archive.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read()
        try:
            body = raw.decode(charset, errors="strict")
        except (UnicodeDecodeError, LookupError) as exc:
            raise FetchDecodeError(
                f"{url} declares charset {charset!r} but its body does not decode as "
                f"that charset ({exc}); refusing to cache a corrupted copy"
            ) from exc
        return TransportResponse(
            body=body,
            status=getattr(response, "status", None),
            content_type=response.headers.get_content_type(),
        )


class Fetcher:
    def __init__(
        self,
        cache_dir: Path,
        delay: float = 2.0,
        transport: Callable[[str], TransportResponse | str] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.delay = delay
        self.transport = transport or _urllib_transport

    def get(self, url: str, name: str) -> FetchResult:
        body_path = self.cache_dir / name
        meta_path = self.cache_dir / f"{name}.meta.json"

        # Both halves are required. A body with no meta file is what a run
        # interrupted mid-write leaves behind, and it carries no provenance --
        # serving it would present an unverifiable page as a cache hit.
        if body_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return FetchResult(
                url=meta["url"],
                name=name,
                body=body_path.read_text(encoding="utf-8"),
                fetched_at=meta["fetched_at"],
                from_cache=True,
                status=meta.get("status"),
                content_type=meta.get("content_type"),
            )

        response = self.transport(url)
        if isinstance(response, str):
            response = TransportResponse(body=response)
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        body_path.write_text(response.body, encoding="utf-8")
        # Status and content-type are recorded so a poisoned cache entry -- a
        # login redirect, an error page served as 200, a non-HTML body -- can
        # still be identified long after the fetch.
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "fetched_at": fetched_at,
                    "status": response.status,
                    "content_type": response.content_type,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        if self.delay:
            time.sleep(self.delay)
        return FetchResult(
            url=url,
            name=name,
            body=response.body,
            fetched_at=fetched_at,
            from_cache=False,
            status=response.status,
            content_type=response.content_type,
        )
