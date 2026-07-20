import json
from types import SimpleNamespace

import pytest

from podcast_editor.pipeline.highlights import (
    MAX_HIGHLIGHT_RESPONSE_TOKENS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    RetryableHighlightDetectionError,
    call_claude,
    detect_highlights,
    enrich_highlights,
    parse_json_response,
)
from podcast_editor.config import Settings
from podcast_editor.jobs import JobStore, new_job_id


def test_parse_json_response_strips_code_fences() -> None:
    payload = parse_json_response(
        """```json
        {"roles":{"SPEAKER_00":"host"},"highlights":[]}
        ```"""
    )

    assert payload["roles"]["SPEAKER_00"] == "host"


def test_parse_json_response_rejects_invalid_shape() -> None:
    with pytest.raises(ValueError):
        parse_json_response('{"not":"the schema"}')


def test_enrich_highlights_adds_ids_and_matching_text() -> None:
    payload = {
        "roles": {"SPEAKER_00": "guest"},
        "topics": ["יזמות", "מימון"],
        "highlights": [
            {
                "start": 10.0,
                "end": 20.0,
                "speaker": "SPEAKER_00",
                "topic": "יזמות",
                "reason": "תובנה חשובה",
                "score": 9,
            }
        ],
    }
    transcript = [
        {"start": 8.0, "end": 12.0, "text": "פתיחה"},
        {"start": 12.1, "end": 19.5, "text": "הרעיון המרכזי"},
        {"start": 25.0, "end": 30.0, "text": "לא רלוונטי"},
    ]

    selection = {"mode": "library", "prompt_version": PROMPT_VERSION}
    enriched = enrich_highlights(payload, transcript, selection)

    assert enriched["highlights"][0]["id"] == "h01"
    assert enriched["highlights"][0]["text"] == "פתיחה הרעיון המרכזי"
    assert enriched["highlights"][0]["topic"] == "יזמות"
    assert enriched["topics"] == ["יזמות", "מימון"]
    assert enriched["selection"] == selection


def test_enrich_highlights_drops_zero_length_and_invalid_ranges() -> None:
    payload = {
        "roles": {},
        "topics": ["Topic"],
        "highlights": [
            {
                "start": 10,
                "end": 10,
                "speaker": "SPEAKER_00",
                "topic": "Topic",
                "reason": "zero length",
                "score": 8,
            },
            {
                "start": 20,
                "end": 25,
                "speaker": "SPEAKER_00",
                "topic": "Topic",
                "reason": "valid",
                "score": 7,
            },
        ],
    }

    enriched = enrich_highlights(payload, [], {"mode": "library"})

    assert len(enriched["highlights"]) == 1
    assert enriched["highlights"][0]["id"] == "h01"
    assert enriched["highlights"][0]["reason"] == "valid"


def test_call_claude_avoids_deprecated_temperature_parameter() -> None:
    captured = {}

    class Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"ok":true}')])

    client = SimpleNamespace(messages=Messages())
    call_claude(
        client,
        "claude-test",
        {"segments": []},
        selection={"topic": "יזמות", "target_minutes": 12},
    )

    assert "temperature" not in captured
    assert captured["max_tokens"] == MAX_HIGHLIGHT_RESPONSE_TOKENS
    content = json.loads(captured["messages"][0]["content"])
    assert content["editorial_preferences"]["topic"] == "יזמות"
    assert content["editorial_preferences"]["target_minutes"] == 12


def test_editorial_prompt_requires_complete_conversational_passages() -> None:
    assert PROMPT_VERSION == 5
    assert "editorially complete, independently listenable passage" in SYSTEM_PROMPT
    assert "Completeness is more important than brevity" in SYSTEM_PROMPT
    assert "Never begin or end mid-sentence" in SYSTEM_PROMPT
    assert "question, premise, setup" in SYSTEM_PROMPT
    assert "through the speaker's reasoning" in SYSTEM_PROMPT
    assert "There is no target or maximum highlight duration" in SYSTEM_PROMPT
    assert "editorial boundary check" in SYSTEM_PROMPT


def test_invalid_provider_json_retries_in_a_new_invocation(monkeypatch, tmp_path) -> None:
    calls = []

    class Messages:
        def create(self, **_kwargs):
            calls.append(1)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="not json")])

    class FakeAnthropic:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 240.0
            assert kwargs["max_retries"] == 0
            self.messages = Messages()

    settings = Settings(
        data_dir=tmp_path, state_backend="filesystem", anthropic_api_key="test-key"
    )
    store = JobStore(settings)
    job_id = new_job_id()
    store.write_json(job_id, "input", {"language": "en"})
    store.write_json(job_id, "transcript", {"duration": 1, "segments": []})
    monkeypatch.setattr("podcast_editor.pipeline.highlights.Anthropic", FakeAnthropic)

    with pytest.raises(RetryableHighlightDetectionError):
        detect_highlights(job_id, store, settings)

    assert len(calls) == 1
