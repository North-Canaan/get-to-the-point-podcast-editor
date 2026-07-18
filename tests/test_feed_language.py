from podcast_editor.pipeline.ingest import normalize_feed_language


def test_feed_language_normalizes_locale_and_legacy_codes() -> None:
    assert normalize_feed_language("en-US") == "en"
    assert normalize_feed_language("he-IL") == "he"
    assert normalize_feed_language("iw_IL") == "he"
    assert normalize_feed_language("eng") == "en"


def test_feed_language_uses_english_when_feed_omits_language() -> None:
    assert normalize_feed_language(None) == "en"
    assert normalize_feed_language("not a language") == "en"
