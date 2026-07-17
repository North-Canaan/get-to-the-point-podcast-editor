from podcast_editor.pipeline.transcribe import collapse_speaker_turns


def test_collapse_speaker_turns_merges_adjacent_same_speaker() -> None:
    turns = collapse_speaker_turns(
        [
            {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "שלום"},
            {"start": 2.4, "end": 5.0, "speaker": "SPEAKER_00", "text": "עולם"},
            {"start": 5.2, "end": 7.0, "speaker": "SPEAKER_01", "text": "תשובה"},
        ]
    )

    assert turns == [
        {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "text": "שלום עולם"},
        {"start": 5.2, "end": 7.0, "speaker": "SPEAKER_01", "text": "תשובה"},
    ]
