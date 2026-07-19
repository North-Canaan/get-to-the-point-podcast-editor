"""Opt-in checks for the deployed RSS delivery chain.

Set PRODUCTION_SMOKE_FEED_URL to a private feed URL in CI or locally. The test is
skipped by default so secrets never need to live in the repository.
"""

import os
from urllib.parse import urlparse
from xml.etree import ElementTree

import httpx
import pytest


FEED_URL = os.getenv("PRODUCTION_SMOKE_FEED_URL")


@pytest.mark.skipif(not FEED_URL, reason="PRODUCTION_SMOKE_FEED_URL is not configured")
def test_latest_feed_enclosures_are_downloadable_from_the_canonical_origin() -> None:
    assert FEED_URL is not None
    expected_origin = urlparse(FEED_URL).netloc
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        feed_response = client.get(FEED_URL)
        assert feed_response.status_code == 200
        assert "application/rss+xml" in feed_response.headers.get("content-type", "")
        root = ElementTree.fromstring(feed_response.content)
        enclosures = root.findall("./channel/item/enclosure")[:3]
        assert enclosures, "the private feed contains no episodes"
        for enclosure in enclosures:
            url = enclosure.attrib["url"]
            assert urlparse(url).scheme == "https"
            assert urlparse(url).netloc == expected_origin
            response = client.head(url)
            assert response.status_code == 200
            assert response.headers.get("content-type", "").startswith("audio/mpeg")
            assert int(response.headers.get("content-length", "0")) > 0
