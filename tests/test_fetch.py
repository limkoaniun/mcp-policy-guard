import email.message
import json

import pytest

from enrolment import fetch as fetch_module
from enrolment.fetch import FetchDecodeError, Fetcher, TransportResponse, _urllib_transport


class FakeTransport:
    def __init__(self, body: str = "<html>hello</html>"):
        self.body = body
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        return self.body


class FakeResponse:
    """Stands in for the object `urllib.request.urlopen` returns."""

    def __init__(self, raw: bytes, content_type: str, status: int = 200):
        self.raw = raw
        self.status = status
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def read(self) -> bytes:
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serving(raw: bytes, content_type: str, status: int = 200):
    def urlopen(request, timeout=None):
        return FakeResponse(raw, content_type, status)

    return urlopen


def test_first_get_hits_the_network(tmp_path):
    transport = FakeTransport()
    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=transport)
    result = fetcher.get("https://example.test/a", "a.html")
    assert result.body == "<html>hello</html>"
    assert result.from_cache is False
    assert transport.calls == ["https://example.test/a"]


def test_second_get_is_served_from_cache(tmp_path):
    transport = FakeTransport()
    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=transport)
    fetcher.get("https://example.test/a", "a.html")
    again = fetcher.get("https://example.test/a", "a.html")
    assert again.from_cache is True
    assert len(transport.calls) == 1


def test_cache_survives_a_new_fetcher_instance(tmp_path):
    Fetcher(cache_dir=tmp_path, delay=0, transport=FakeTransport()).get(
        "https://example.test/a", "a.html"
    )
    second_transport = FakeTransport()
    result = Fetcher(cache_dir=tmp_path, delay=0, transport=second_transport).get(
        "https://example.test/a", "a.html"
    )
    assert result.from_cache is True
    assert second_transport.calls == []


def test_provenance_is_written_alongside_the_body(tmp_path):
    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=FakeTransport())
    result = fetcher.get("https://example.test/a", "a.html")
    meta = json.loads((tmp_path / "a.html.meta.json").read_text())
    assert meta["url"] == "https://example.test/a"
    assert meta["fetched_at"] == result.fetched_at
    assert (tmp_path / "a.html").read_text() == "<html>hello</html>"


# A typographic apostrophe: one byte (0x92) in windows-1252, two in UTF-8, and
# U+FFFD if a windows-1252 body is decoded as UTF-8 with errors="replace".
DESCRIPTOR = "Master’s Coursework courses owned by FEIT © UTS"


def test_body_is_decoded_with_the_declared_charset(monkeypatch):
    """A windows-1252 page must not lose its apostrophe to U+FFFD.

    The corrupted body is written straight to the committed cache, so offline
    re-parsing could never recover the original wording.
    """
    monkeypatch.setattr(
        fetch_module.urllib.request,
        "urlopen",
        serving(DESCRIPTOR.encode("windows-1252"), "text/html; charset=windows-1252"),
    )
    response = _urllib_transport("https://example.test/a")
    assert response.body == DESCRIPTOR
    assert "�" not in response.body
    assert response.status == 200
    assert response.content_type == "text/html"


def test_body_that_contradicts_its_declared_charset_fails_loudly(monkeypatch):
    monkeypatch.setattr(
        fetch_module.urllib.request,
        "urlopen",
        serving(b"\xff\xfe not utf-8 at all", "text/html; charset=utf-8"),
    )
    with pytest.raises(FetchDecodeError) as excinfo:
        _urllib_transport("https://example.test/a")
    assert "utf-8" in str(excinfo.value)


def test_charset_defaults_to_utf8_when_the_server_declares_none(monkeypatch):
    monkeypatch.setattr(
        fetch_module.urllib.request,
        "urlopen",
        serving(DESCRIPTOR.encode("utf-8"), "text/html"),
    )
    assert _urllib_transport("https://example.test/a").body == DESCRIPTOR


def test_status_and_content_type_are_recorded_in_the_meta_file(tmp_path):
    """A poisoned cache entry stays identifiable after the fetch."""

    def transport(url: str) -> TransportResponse:
        return TransportResponse(body="<html>login</html>", status=200, content_type="text/html")

    fetcher = Fetcher(cache_dir=tmp_path, delay=0, transport=transport)
    result = fetcher.get("https://example.test/a", "a.html")
    meta = json.loads((tmp_path / "a.html.meta.json").read_text())
    assert meta["status"] == 200
    assert meta["content_type"] == "text/html"
    assert result.status == 200
    assert fetcher.get("https://example.test/a", "a.html").content_type == "text/html"


def test_body_without_a_meta_file_is_treated_as_a_cache_miss(tmp_path):
    """A run interrupted between the two writes leaves exactly this state.

    The first real build is a long, interruptible fetch, so this is the cache
    state most likely to actually occur. A body with no meta file has no
    recorded URL or timestamp: serving it would present an unverifiable page
    as a provenance-carrying cache hit.

    Guard, not a regression -- the behaviour is already correct, but nothing
    pinned it.
    """
    (tmp_path / "a.html").write_text("<html>half-written</html>", encoding="utf-8")
    transport = FakeTransport()
    result = Fetcher(cache_dir=tmp_path, delay=0, transport=transport).get(
        "https://example.test/a", "a.html"
    )
    assert result.from_cache is False
    assert result.body == "<html>hello</html>"
    assert transport.calls == ["https://example.test/a"]
    assert json.loads((tmp_path / "a.html.meta.json").read_text())["url"] == (
        "https://example.test/a"
    )
