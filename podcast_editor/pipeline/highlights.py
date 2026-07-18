import json
import re
from typing import Any

from anthropic import Anthropic

from ..config import Settings
from ..jobs import JobStore

SYSTEM_PROMPT = """You are editing a long, unedited Hebrew-language interview podcast into a tight highlight reel. You will receive a diarized transcript: a list of segments, each with an id, start/end time in seconds, a speaker label, and Hebrew text.

First, infer the conversational roles. The host/interviewer is the speaker who asks questions, introduces topics, and steers; the guest(s) answer at length and supply the substance. Return your role mapping.

Then select the highlight-worthy moments. You are optimizing for a listener who wants the guest's actual insight and none of the host's filler. Prioritize segments where the guest: makes a substantive or surprising claim, reveals specific first-hand detail, pushes back or disagrees, or delivers a self-contained idea that moves the conversation forward. Down-weight host monologues, small talk, throat-clearing, repetition, ad reads, and crosstalk. Keep the interviewer only when their question is required to make the guest's answer intelligible.

Prefer segments that stand on their own. When a strong answer starts a few seconds before or after a segment boundary, extend the start/end to capture the complete thought. Aim for 8–15 highlights for a typical episode; return more only if the material genuinely warrants it. Order them by importance, not by timestamp.

Write every "reason" field in Hebrew. Return ONLY valid JSON, no preamble, no markdown fences, matching this schema exactly:
```
{
  "roles": { "SPEAKER_00": "host" | "guest" | "other", ... },
  "highlights": [
    { "start": <number>, "end": <number>, "speaker": "<label>", "reason": "<hebrew string>", "score": <1-10> }
  ]
}
```"""

MAX_HIGHLIGHT_RESPONSE_TOKENS = 4000


def detect_highlights(job_id: str, store: JobStore, settings: Settings) -> dict:
    transcript = store.read_json(job_id, "transcript")
    if not transcript:
        raise RuntimeError("transcript.json is required before highlight detection")
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for highlight detection")

    client = Anthropic(api_key=settings.anthropic_api_key)
    response_text = call_claude(client, settings.anthropic_model, transcript)
    try:
        payload = parse_json_response(response_text)
    except ValueError:
        response_text = call_claude(
            client,
            settings.anthropic_model,
            transcript,
            reminder="Return only valid JSON. Do not include markdown fences or commentary.",
        )
        payload = parse_json_response(response_text)

    enriched = enrich_highlights(payload, transcript["segments"])
    store.write_json(job_id, "highlights", enriched)
    return enriched


def call_claude(
    client: Anthropic, model: str, transcript: dict, reminder: str | None = None
) -> str:
    content = json.dumps({"segments": transcript["segments"]}, ensure_ascii=False)
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


def enrich_highlights(payload: dict, transcript_segments: list[dict]) -> dict:
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
    return {"roles": payload.get("roles", {}), "highlights": highlights}


def matching_text(transcript_segments: list[dict], start: float, end: float) -> str:
    texts = []
    for segment in transcript_segments:
        segment_start = float(segment["start"])
        segment_end = float(segment["end"])
        if segment_end >= start and segment_start <= end:
            texts.append(segment.get("text", ""))
    return " ".join(texts).strip()
