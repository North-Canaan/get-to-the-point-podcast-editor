import json
import re
from typing import Any

from anthropic import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from anthropic import Anthropic

from ..config import Settings
from ..jobs import JobStore

PROMPT_VERSION = 5

SYSTEM_PROMPT = """You are editing a long, unedited podcast into a tight highlight reel. You will receive the language declared by the podcast's RSS feed and a diarized transcript: a list of segments, each with an id, start/end time in seconds, a speaker label, and text, plus editorial preferences.

First, infer the conversational roles. The host/interviewer is the speaker who asks questions, introduces topics, and steers; the guest(s) answer at length and supply the substance. Return your role mapping.

Identify concise topic labels, written in the episode's declared language, that collectively describe the substantive topics covered in the episode. Use a stable, canonical label for each topic.

Then build an exhaustive highlight library. For a long episode this may contain 50–100 highlights; do not stop after finding only the best few and do not optimize for a total edit duration. Capture every distinct, substantive moment that a human editor might reasonably choose. Do not optimize highlights for shortness or treat an isolated quote, claim, or sentence as a highlight.

A highlight is an editorially complete, independently listenable passage that contains one complete thought, argument, explanation, story, or conversational exchange as it was expressed on the podcast. It must make sense to a listener who has not heard the material immediately before or after it. Completeness is more important than brevity; a strong highlight may last several minutes and may contain multiple speakers.

Choose boundaries like a human audio editor:
- First locate the valuable idea or payoff. Then scan backward to include the natural beginning: the question, premise, setup, definition, or earlier turn required to understand it.
- Scan forward through the speaker's reasoning, examples, qualifications, and relevant back-and-forth until the idea reaches its conclusion or the conversation naturally transitions.
- Start and end on natural transcript-segment boundaries. Never begin or end mid-sentence, mid-answer, mid-story, during unresolved crosstalk, or before a question receives its relevant answer.
- Do not leave dangling pronouns, unexplained references, missing premises, or an ending that promises a conclusion outside the clip.
- Include the interviewer or another speaker whenever their question, challenge, or response is necessary to preserve the meaning and conversational flow.
- Prefer a longer coherent passage over a shorter fragment. There is no target or maximum highlight duration.
- Split a long discussion only at a genuine editorial transition, and only when every resulting highlight remains complete and independently understandable. Avoid duplicate highlights and unnecessary overlap.

Prioritize passages where the guest makes a substantive or surprising claim, reveals specific first-hand detail, pushes back or disagrees, explains a mechanism, tells a meaningful story, or develops an idea that moves the conversation forward. Down-weight host monologues, small talk, throat-clearing, repetition, ad reads, and irrelevant crosstalk.

Assign every highlight exactly one canonical topic label from the top-level topics list. Topic labels are navigation filters for the highlight library, so every topic should have at least one highlight and every highlight must belong to a topic.

Before returning each highlight, perform an editorial boundary check: could someone listen only from `start` to `end` and understand the setup, substance, and resolution without hearing the neighboring transcript? If not, expand or adjust its boundaries. Order highlights by editorial importance, not by timestamp. The `speaker` field should name the primary speaker, even when the complete passage contains multiple speakers.

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
    for highlight in payload.get("highlights", []):
        start = float(highlight["start"])
        end = float(highlight["end"])
        if not 0 <= start < end <= 86_400:
            continue
        highlights.append(
            {
                "id": f"h{len(highlights) + 1:02d}",
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
