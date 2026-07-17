from podcast_editor.pipeline.no_worker import collapse_assemblyai_utterances


def test_collapse_assemblyai_utterances_maps_speakers_and_times() -> None:
    payload = {
        "id": "tx_123",
        "status": "completed",
        "language_code": "he",
        "audio_duration": 12,
        "utterances": [
            {"speaker": "A", "start": 1000, "end": 2500, "text": "שלום עולם"},
            {"speaker": "B", "start": 3000, "end": 4200, "text": "תשובה קצרה"},
        ],
    }

    transcript = collapse_assemblyai_utterances(payload)

    assert transcript["duration"] == 12
    assert transcript["segments"][0] == {
        "id": 0,
        "start": 1.0,
        "end": 2.5,
        "speaker": "SPEAKER_A",
        "text": "שלום עולם",
    }
    assert transcript["segments"][1]["speaker"] == "SPEAKER_B"
