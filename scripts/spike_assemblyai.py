#!/usr/bin/env python3
"""Spike AssemblyAI Hebrew diarization against one audio URL.

Usage:
  ASSEMBLYAI_API_KEY=... python scripts/spike_assemblyai.py \
    --audio-url https://example.com/episode.mp3 \
    --out data/spikes/assemblyai-hebrew.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx


API_BASE = "https://api.assemblyai.com/v2"


def submit_transcript(api_key: str, audio_url: str, webhook_url: str | None = None) -> str:
    payload: dict[str, Any] = {
        "audio_url": audio_url,
        "language_code": "he",
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True,
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            f"{API_BASE}/transcript",
            headers=auth_headers(api_key),
            json=payload,
        )
        response.raise_for_status()
        return str(response.json()["id"])


def poll_transcript(
    api_key: str,
    transcript_id: str,
    poll_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    with httpx.Client(timeout=30.0) as client:
        while time.monotonic() < deadline:
            response = client.get(
                f"{API_BASE}/transcript/{transcript_id}",
                headers=auth_headers(api_key),
            )
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status", "unknown")
            if status != last_status:
                print(f"AssemblyAI status: {status}", file=sys.stderr)
                last_status = status
            if status == "completed":
                return payload
            if status == "error":
                raise RuntimeError(payload.get("error") or "AssemblyAI transcription failed")
            time.sleep(poll_seconds)
    raise TimeoutError(f"Timed out waiting for transcript {transcript_id}")


def auth_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": api_key, "Content-Type": "application/json"}


def collapse_utterances(payload: dict[str, Any]) -> dict[str, Any]:
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
        "assemblyai_id": payload["id"],
        "language_code": payload.get("language_code"),
        "language_confidence": payload.get("language_confidence"),
        "duration": payload.get("audio_duration"),
        "raw_status": payload.get("status"),
        "segments": segments,
    }


def milliseconds_to_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    return round(float(value) / 1000.0, 3)


def summarize(transcript: dict[str, Any]) -> dict[str, Any]:
    segments = transcript["segments"]
    speakers = sorted({segment["speaker"] for segment in segments})
    text_chars = sum(len(segment["text"]) for segment in segments)
    hebrew_chars = sum(
        1 for segment in segments for char in segment["text"] if "\u0590" <= char <= "\u05ff"
    )
    return {
        "assemblyai_id": transcript["assemblyai_id"],
        "language_code": transcript.get("language_code"),
        "language_confidence": transcript.get("language_confidence"),
        "duration": transcript.get("duration"),
        "segment_count": len(segments),
        "speaker_count": len(speakers),
        "speakers": speakers,
        "hebrew_character_ratio": round(hebrew_chars / text_chars, 3) if text_chars else 0,
        "passes_basic_hebrew_diarization_gate": len(speakers) >= 2 and hebrew_chars > 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Spike AssemblyAI Hebrew diarization.")
    parser.add_argument("--audio-url", required=True, help="Publicly accessible audio URL.")
    parser.add_argument("--out", default="data/spikes/assemblyai-hebrew.json")
    parser.add_argument("--summary-out", default="data/spikes/assemblyai-hebrew-summary.json")
    parser.add_argument("--transcript-id", help="Reuse an existing AssemblyAI transcript id.")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    parser.add_argument("--webhook-url")
    args = parser.parse_args()

    api_key = os.getenv("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise SystemExit("ASSEMBLYAI_API_KEY is required")

    transcript_id = args.transcript_id or submit_transcript(
        api_key, args.audio_url, webhook_url=args.webhook_url
    )
    print(f"AssemblyAI transcript id: {transcript_id}", file=sys.stderr)
    raw_payload = poll_transcript(api_key, transcript_id, args.poll_seconds, args.timeout_seconds)
    transcript = collapse_utterances(raw_payload)
    summary = summarize(transcript)

    out_path = Path(args.out)
    summary_path = Path(args.summary_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
