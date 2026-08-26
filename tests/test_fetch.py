import json

from enrolment.fetch import Fetcher


class FakeTransport:
    def __init__(self, body: str = "<html>hello</html>"):
        self.body = body
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        return self.body


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
