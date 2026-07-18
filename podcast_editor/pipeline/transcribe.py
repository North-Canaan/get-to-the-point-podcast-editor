from collections.abc import Iterable

from ..config import Settings
from ..jobs import JobStore


def transcribe_and_diarize(job_id: str, store: JobStore, settings: Settings) -> dict:
    try:
        import torch
        import whisperx
    except ImportError as exc:
        raise RuntimeError(
            "WhisperX worker dependencies are not installed. "
            "Install with: pip install -r requirements-worker.txt"
        ) from exc

    audio_path = store.artifact_path(job_id, "audio16k")
    input_payload = store.read_json(job_id, "input") or {}
    language = str(input_payload.get("language") or "en")
    device = settings.whisper_device
    compute_type = "float16" if device == "cuda" and torch.cuda.is_available() else "int8"

    model = whisperx.load_model(
        settings.whisper_model, device, language=language, compute_type=compute_type
    )
    audio = whisperx.load_audio(str(audio_path))
    result = model.transcribe(audio, language=language)

    align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )

    if not settings.hf_token:
        raise RuntimeError("HF_TOKEN is required for pyannote diarization")
    diarize_model = whisperx.DiarizationPipeline(use_auth_token=settings.hf_token, device=device)
    diarized = diarize_model(audio)
    assigned = whisperx.assign_word_speakers(diarized, aligned)
    turns = collapse_speaker_turns(assigned.get("segments", []))
    payload = {
        "duration": float(input_payload.get("duration") or 0),
        "segments": [
            {
                "id": index,
                "start": turn["start"],
                "end": turn["end"],
                "speaker": turn["speaker"],
                "text": turn["text"],
            }
            for index, turn in enumerate(turns)
        ],
    }
    store.write_json(job_id, "transcript", payload)
    return payload


def collapse_speaker_turns(segments: Iterable[dict]) -> list[dict]:
    turns: list[dict] = []
    for segment in segments:
        speaker = segment.get("speaker") or "SPEAKER_UNKNOWN"
        text = clean_text(segment.get("text", ""))
        if not text:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        if turns and turns[-1]["speaker"] == speaker and start - turns[-1]["end"] <= 1.0:
            turns[-1]["end"] = end
            turns[-1]["text"] = clean_text(f"{turns[-1]['text']} {text}")
        else:
            turns.append({"start": start, "end": end, "speaker": speaker, "text": text})
    return turns


def clean_text(value: str) -> str:
    return " ".join(value.split())
