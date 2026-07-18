from pathlib import Path

from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore, new_job_id
from podcast_editor.pipeline import no_worker
from podcast_editor.pipeline.no_worker import collapse_assemblyai_utterances
from podcast_editor.schemas import JobStatus


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


def test_submit_job_preserves_selected_episode_title(monkeypatch, tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        state_backend="filesystem",
        assemblyai_api_key="test-key",
    )
    store = JobStore(settings)
    job_id = new_job_id()
    monkeypatch.setattr(no_worker, "resolve_audio_url", lambda url: url)
    monkeypatch.setattr(no_worker, "submit_assemblyai_transcript", lambda key, url: "tx_123")

    payload = no_worker.submit_no_worker_job(
        job_id,
        "https://cdn.example.com/episode.mp3",
        store,
        settings,
        "Selected Episode",
    )

    assert payload["episode_title"] == "Selected Episode"
    assert store.get_status(job_id).status == JobStatus.transcribing
