import json
import re
from typing import Any

from anthropic import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from anthropic import Anthropic

from ..config import Settings
from ..jobs import JobStore

PROMPT_VERSION = 4

SYSTEM_PROMPT = """You are editing a long, unedited podcast into a tight highlight reel. You will receive the language declared by the podcast's RSS feed and a diarized transcript: a list of segments, each with an id, start/end time in seconds, a speaker label, and text, plus editorial preferences.

First, infer the conversational roles. The host/interviewer is the speaker who asks questions, introduces topics, and steers; the guest(s) answer at length and supply the substance. Return your role mapping.

Identify concise topic labels, written in the episode's declared language, that collectively describe the substantive topics covered in the episode. Use a stable, canonical label for each topic.

Then build an exhaustive highlight library. For a long episode this may contain 50–100 highlights; do not stop after finding only the best few and do not optimize for a total edit duration. Capture every distinct, substantive, self-contained moment that a human editor might reasonably choose. Split long discussions into separate highlights when they contain multiple independently useful ideas, but do not create duplicates or filler.

Prioritize moments where the guest makes a substantive or surprising claim, reveals specific first-hand detail, pushes back or disagrees, explains a mechanism, tells a meaningful story, or delivers a self-contained idea that moves the conversation forward. Down-weight host monologues, small talk, throat-clearing, repetition, ad reads, and crosstalk. Keep the interviewer only when their question is required to make the guest's answer intelligible.

Assign every highlight exactly one canonical topic label from the top-level topics list. Topic labels are navigation filters for the highlight library, so every topic should have at least one highlight and every highlight must belong to a topic.

Prefer segments that stand on their own. When a strong answer starts a few seconds before or after a segment boundary, extend the start/end to capture the complete thought. Order them by importance, not by timestamp.

Write every "reason" field in the episode's declared language. Return ONLY valid JSON, no preamble, no markdown fences, matching this schema exactly:
```
{
  "roles": { "SPEAKER_00": "host" | "guest" | "other", ... },
  "topics": ["<topic in the episode language>", ...],
  "highlights": [
    { "start": <number>, "end": <number>, "speaker": "<label>", "topic": "<exact topic label from topics>", "reason": "<hebrew string>", "score": <1-10> }
  ]
}
```"""

MAX_HIGHLIGHT_RESPONSE_TOKENS = 20000
HIGHLIGHT_PROVIDER_TIMEOUT_SECONDS = 240.0


class RetryableHighlightDetectionError(RuntimeError):
    """A provider failure that should leave the job available for another attempt."""


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

    client = Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=HIGHLIGHT_PROVIDER_TIMEOUT_SECONDS,
        max_retries=0,
    )
    selection = {"mode": "library", "prompt_version": PROMPT_VERSION}
    language = str(input_payload.get("language") or "en")
    try:
        response_text = call_claude(
            client, settings.anthropic_model, transcript, language=language, selection=selection
        )
        payload = parse_json_response(response_text)
    except (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError) as exc:
        raise RetryableHighlightDetectionError(
            "Highlight provider is temporarily unavailable; the job will retry."
        ) from exc
    except ValueError as exc:
        # Do not spend the remainder of the serverless request on a second 20k-token
        # generation. Release the lease and retry in a fresh invocation instead.
        raise RetryableHighlightDetectionError(
            "Highlight response was incomplete; the job will retry."
        ) from exc

    enriched = enrich_highlights(payload, transcript["segments"], selection)
    store.write_json(job_id, "highlights", enriched)
    return enriched


def call_claude(
    client: Anthropic,
    model: str,
    transcript: dict,
    language: str = "en",
    selection: dict | None = None,
) -> str:
    content = json.dumps(
        {
            "language": language,
            "editorial_preferences": selection or {},
            "segments": transcript["segments"],
        },
        ensure_ascii=False,
    )
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
                "topic": str(highlight.get("topic") or "Other"),
                "reason": str(highlight["reason"]),
                "score": int(highlight["score"]),
                "text": matching_text(transcript_segments, start, end),
            }
        )
    topics = [str(topic) for topic in payload.get("topics", [])]
    for highlight in highlights:
        if highlight["topic"] not in topics:
            topics.append(highlight["topic"])
    return {
        "roles": payload.get("roles", {}),
        "topics": topics,
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
