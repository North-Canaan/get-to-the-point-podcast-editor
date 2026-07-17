import json
from typing import Any

import feedparser
import httpx

from ..config import Settings
from ..jobs import JobStore
from ..schemas import JobStatus
from .highlights import detect_highlights

ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"


def submit_no_worker_job(job_id: str, source_url: str, store: JobStore, settings: Settings) -> dict:
    if not settings.assemblyai_api_key:
        store.set_status(job_id, JobStatus.error, error="ASSEMBLYAI_API_KEY is required")
        raise RuntimeError("ASSEMBLYAI_API_KEY is required")

    resolved_url = resolve_audio_url(source_url)
    transcript_id = submit_assemblyai_transcript(settings.assemblyai_api_key, resolved_url)
    input_payload = {
        "source_url": source_url,
        "resolved_audio_url": resolved_url,
        "assemblyai_transcript_id": transcript_id,
        "provider": "assemblyai",
    }
    store.write_json(job_id, "input", input_payload)
    store.set_status(
        job_id,
        JobStatus.transcribing,
        source_url=source_url,
        extra={
            "resolved_audio_url": resolved_url,
            "assemblyai_transcript_id": transcript_id,
        },
    )
    return input_payload


def advance_no_worker_job(job_id: str, store: JobStore, settings: Settings) -> None:
    status = store.get_status(job_id)
    if status.status != JobStatus.transcribing:
        return
    input_payload = store.read_json(job_id, "input") or {}
    transcript_id = input_payload.get("assemblyai_transcript_id")
    if not transcript_id:
        store.set_status(job_id, JobStatus.error, error="missing AssemblyAI transcript id")
        return
    if not settings.assemblyai_api_key:
        store.set_status(job_id, JobStatus.error, error="ASSEMBLYAI_API_KEY is required")
        return

    assembly = get_assemblyai_transcript(settings.assemblyai_api_key, transcript_id)
    assembly_status = assembly.get("status")
    if assembly_status in {"queued", "processing"}:
        return
    if assembly_status == "error":
        store.set_status(
            job_id,
            JobStatus.error,
            error=str(assembly.get("error") or "AssemblyAI transcription failed"),
        )
        return
    if assembly_status != "completed":
        return

    transcript = collapse_assemblyai_utterances(assembly)
    store.write_json(job_id, "transcript", transcript)
    store.set_status(job_id, JobStatus.detecting_highlights)
    try:
        detect_highlights(job_id, store, settings)
    except Exception as exc:
        store.set_status(job_id, JobStatus.error, error=str(exc))
        return
    store.set_status(job_id, JobStatus.needs_review)


def resolve_audio_url(source_url: str) -> str:
    text = ""
    try:
        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            response = client.head(source_url)
            content_type = response.headers.get("content-type", "")
            if "xml" in content_type or "rss" in content_type or looks_like_feed_url(source_url):
                text = client.get(source_url).text
    except httpx.HTTPError:
        if looks_like_feed_url(source_url):
            try:
                with httpx.Client(follow_redirects=True, timeout=20.0) as client:
                    text = client.get(source_url).text
            except httpx.HTTPError:
                text = ""

    if not text:
        return source_url

    parsed = feedparser.parse(text)
    if parsed.entries:
        entry = parsed.entries[0]
        for enclosure in getattr(entry, "enclosures", []) or []:
            href = enclosure.get("href")
            if href:
                return str(href)
        for link in getattr(entry, "links", []) or []:
            if link.get("rel") == "enclosure" and link.get("href"):
                return str(link["href"])
    return source_url


def looks_like_feed_url(source_url: str) -> bool:
    lower = source_url.lower()
    return lower.endswith(".xml") or lower.endswith(".rss") or "feed" in lower or "rss" in lower


def submit_assemblyai_transcript(api_key: str, audio_url: str) -> str:
    payload = {
        "audio_url": audio_url,
        "language_code": "he",
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{ASSEMBLYAI_BASE}/transcript",
            headers=assemblyai_headers(api_key),
            json=payload,
        )
        response.raise_for_status()
        return str(response.json()["id"])


def get_assemblyai_transcript(api_key: str, transcript_id: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{ASSEMBLYAI_BASE}/transcript/{transcript_id}",
            headers=assemblyai_headers(api_key),
        )
        response.raise_for_status()
        return response.json()


def assemblyai_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": api_key, "Content-Type": "application/json"}


def collapse_assemblyai_utterances(payload: dict[str, Any]) -> dict:
    utterances = payload.get("utterances") or []
    segments = []
    for index, utterance in enumerate(utterances):
        speaker = utterance.get("speaker")
        segments.append(
            {
                "id": index,
                "start": milliseconds_to_seconds(utterance.get("start")),
                "end": milliseconds_to_seconds(utterance.get("end")),
                "speaker": f"SPEAKER_{speaker}" if speaker is not None else "SPEAKER_UNKNOWN",
                "text": " ".join(str(utterance.get("text") or "").split()),
            }
        )
    return {
        "duration": float(payload.get("audio_duration") or 0),
        "segments": segments,
        "provider": "assemblyai",
        "provider_payload": {
            "id": payload.get("id"),
            "language_code": payload.get("language_code"),
            "status": payload.get("status"),
        },
    }


def milliseconds_to_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    return round(float(value) / 1000.0, 3)


def transcript_to_json(transcript: dict) -> str:
    return json.dumps(transcript, ensure_ascii=False, indent=2)
