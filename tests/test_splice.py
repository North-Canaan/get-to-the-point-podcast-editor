from podcast_editor.pipeline.splice import PADDING_SECONDS


def test_padding_constant_matches_spec() -> None:
    assert PADDING_SECONDS == 0.3
