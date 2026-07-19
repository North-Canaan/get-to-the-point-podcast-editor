import httpx
import pytest

from podcast_editor.pipeline.ingest import IngestError, list_feed_episodes


def test_list_feed_episodes_returns_all_enclosures(monkeypatch) -> None:
    rss = b"""<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Test Show</title>
      <item><title>Episode Two</title><pubDate>Fri, 17 Jul 2026 10:00:00 GMT</pubDate>
        <enclosure url="https://cdn.example.com/two.mp3" type="audio/mpeg" /></item>
      <item><title>Episode One</title>
        <enclosure url="https://cdn.example.com/one.mp3" type="audio/mpeg" /></item>
    </channel></rss>"""

    requested_max_bytes = None

    def fake_get(method, url, *, max_bytes):
        nonlocal requested_max_bytes
        requested_max_bytes = max_bytes
        return httpx.Response(200, content=rss, request=httpx.Request("GET", url))

    monkeypatch.setattr("podcast_editor.pipeline.ingest.public_http_request", fake_get)
    result = list_feed_episodes("https://example.com/feed.xml")

    assert result["title"] == "Test Show"
    assert [episode["title"] for episode in result["episodes"]] == ["Episode Two", "Episode One"]
    assert result["episodes"][0]["audio_url"] == "https://cdn.example.com/two.mp3"
    assert requested_max_bytes == 25 * 1024 * 1024


def test_list_feed_episodes_rejects_feed_without_audio(monkeypatch) -> None:
    rss = b'<rss version="2.0"><channel><title>Empty</title><item><title>Post</title></item></channel></rss>'

    def fake_get(method, url, *, max_bytes):
        return httpx.Response(200, content=rss, request=httpx.Request("GET", url))

    monkeypatch.setattr("podcast_editor.pipeline.ingest.public_http_request", fake_get)
    with pytest.raises(IngestError, match="no podcast episodes"):
        list_feed_episodes("https://example.com/feed.xml")
