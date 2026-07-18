import json
import re
from typing import Any

from anthropic import Anthropic

from ..config import Settings
from ..jobs import JobStore

PROMPT_VERSION = 3

SYSTEM_PROMPT = """You are editing a long, unedited podcast into a tight highlight reel. You will receive the language declared by the podcast's RSS feed and a diarized transcript: a list of segments, each with an id, start/end time in seconds, a speaker label, and text, plus editorial preferences.

First, infer the conversational roles. The host/interviewer is the speaker who asks questions, introduces topics, and steers; the guest(s) answer at length and supply the substance. Return your role mapping.

Identify 5–12 concise topic labels, written in the episode's declared language, that collectively describe the substantive topics covered in the episode.

Then select the highlight-worthy moments. You are optimizing for a listener who wants the guest's actual insight and none of the host's filler. Prioritize segments where the guest: makes a substantive or surprising claim, reveals specific first-hand detail, pushes back or disagrees, or delivers a self-contained idea that moves the conversation forward. Down-weight host monologues, small talk, throat-clearing, repetition, ad reads, and crosstalk. Keep the interviewer only when their question is required to make the guest's answer intelligible. Aim for the requested total duration. If a topic is selected, exclude unrelated moments and include every substantive highlight about that topic, using the duration as a target rather than inventing or truncating material.

Prefer segments that stand on their own. When a strong answer starts a few seconds before or after a segment boundary, extend the start/end to capture the complete thought. Order them by importance, not by timestamp.

Write every "reason" field in the episode's declared language. Return ONLY valid JSON, no preamble, no markdown fences, matching this schema exactly:
```
{
  "roles": { "SPEAKER_00": "host" | "guest" | "other", ... },
  "topics": ["<topic in the episode language>", ...],
  "highlights": [
    { "start": <number>, "end": <number>, "speaker": "<label>", "reason": "<hebrew string>", "score": <1-10> }
  ]
}
```"""

MAX_HIGHLIGHT_RESPONSE_TOKENS = 12000


def detect_highlights(
    job_id: str,
    store: JobStore,
    settings: Settings,
    topic: str | None = None,
    target_minutes: int = 15,
) -> dict:
    transcript = store.read_json(job_id, "transcript")
    input_payload = store.read_json(job_id, "input") or {}
    if not transcript:
        raise RuntimeError("transcript.json is required before highlight detection")
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for highlight detection")

    client = Anthropic(api_key=settings.anthropic_api_key)
    selection = {"topic": topic, "target_minutes": target_minutes, "prompt_version": PROMPT_VERSION}
    language = str(input_payload.get("language") or "en")
    response_text = call_claude(
        client, settings.anthropic_model, transcript, language=language, selection=selection
    )
    try:
        payload = parse_json_response(response_text)
    except ValueError:
        response_text = call_claude(
            client,
            settings.anthropic_model,
            transcript,
            language=language,
            selection=selection,
            reminder="Return only valid JSON. Do not include markdown fences or commentary.",
        )
        payload = parse_json_response(response_text)

    enriched = enrich_highlights(payload, transcript["segments"], selection)
    store.write_json(job_id, "highlights", enriched)
    return enriched


def call_claude(
    client: Anthropic,
    model: str,
    transcript: dict,
    language: str = "en",
    selection: dict | None = None,
    reminder: str | None = None,
) -> str:
    content = json.dumps(
        {
            "language": language,
            "editorial_preferences": selection or {},
            "segments": transcript["segments"],
        },
        ensure_ascii=False,
    )
    if reminder:
        content = f"{reminder}\n\n{content}"
    message = client.messages.create(
        model=model,
        max_tokens=MAX_HIGHLIGHT_RESPONSE_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    chunks = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    return "\n".join(chunks)


def parse_json_response(value: str) -> dict[str, Any]:
    cleaned = strip_code_fences(value).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Claude response was not valid JSON") from exc
    if not isinstance(payload, dict) or "roles" not in payload or "highlights" not in payload:
        raise ValueError("Claude response did not match expected schema")
    return payload


def strip_code_fences(value: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.IGNORECASE | re.DOTALL)


def enrich_highlights(
    payload: dict, transcript_segments: list[dict], selection: dict | None = None
) -> dict:
    highlights = []
    for index, highlight in enumerate(payload.get("highlights", []), start=1):
        start = float(highlight["start"])
        end = float(highlight["end"])
        highlights.append(
            {
                "id": f"h{index:02d}",
                "start": start,
                "end": end,
                "speaker": str(highlight["speaker"]),
                "reason": str(highlight["reason"]),
                "score": int(highlight["score"]),
                "text": matching_text(transcript_segments, start, end),
            }
        )
    return {
        "roles": payload.get("roles", {}),
        "topics": [str(topic) for topic in payload.get("topics", [])],
        "selection": selection or {},
        "highlights": highlights,
    }


def matching_text(transcript_segments: list[dict], start: float, end: float) -> str:
    texts = []
    for segment in transcript_segments:
        segment_start = float(segment["start"])
        segment_end = float(segment["end"])
        if segment_end >= start and segment_start <= end:
            texts.append(segment.get("text", ""))
    return " ".join(texts).strip()
