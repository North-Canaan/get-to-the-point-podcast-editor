import pytest

from podcast_editor.automatic import (
    Recipe,
    expected_output_seconds,
    normalize_url,
    select_highlights,
    source_episode_identity,
)


def test_normalize_url_removes_fragment_default_port_and_sorts_query() -> None:
    assert normalize_url("HTTPS://Example.COM:443/feed?z=2&a=1#top") == "https://example.com/feed?a=1&z=2"


def test_identity_prefers_guid_and_falls_back_to_enclosure() -> None:
    by_guid = source_episode_identity("https://example.com/feed", " episode-1 ", "https://cdn.test/a.mp3")
    assert by_guid == source_episode_identity("https://example.com/feed", "episode-1", "https://cdn.test/changed.mp3")
    by_enclosure = source_episode_identity("https://example.com/feed", None, "https://CDN.test:443/a.mp3#x")
    assert by_enclosure == source_episode_identity("https://example.com/feed", "", "https://cdn.test/a.mp3")
    assert by_guid != by_enclosure


def test_recipe_rejects_values_outside_v1_contract() -> None:
    with pytest.raises(ValueError):
        Recipe.from_dict({"target_minutes": 20})
    with pytest.raises(ValueError):
        Recipe.from_dict({"transition_seconds": 0.25})


def test_selection_filters_ranks_removes_overlap_and_restores_time_order() -> None:
    highlights = [
        {"id": "early", "start": 10, "end": 610, "score": 8, "topic": "Tech"},
        {"id": "overlap", "start": 20, "end": 300, "score": 10, "topic": "Tech"},
        {"id": "late", "start": 1000, "end": 1600, "score": 9, "topic": "Tech"},
        {"id": "low", "start": 2000, "end": 2600, "score": 6, "topic": "Tech"},
        {"id": "wrong-topic", "start": 3000, "end": 3600, "score": 10, "topic": "Art"},
    ]
    selected = select_highlights(
        highlights, Recipe(target_minutes=15, topics=("Tech",), minimum_score=7)
    )
    assert [item["id"] for item in selected] == ["overlap", "late"]
    assert expected_output_seconds(selected, 0.5) == 880.5


def test_selection_permits_one_complete_highlight_to_exceed_target() -> None:
    selected = select_highlights(
        [{"id": "long", "start": 0, "end": 1200, "score": 9, "topic": "Tech"}],
        Recipe(target_minutes=15),
    )
    assert selected[0]["end"] == 1200
