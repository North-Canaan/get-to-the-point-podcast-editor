import json
from types import SimpleNamespace

import pytest

from podcast_editor.pipeline.highlights import (
    MAX_HIGHLIGHT_RESPONSE_TOKENS,
    call_claude,
    enrich_highlights,
    parse_json_response,
)


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

    selection = {"topic": "יזמות", "target_minutes": 10, "prompt_version": 3}
    enriched = enrich_highlights(payload, transcript, selection)

    assert enriched["highlights"][0]["id"] == "h01"
    assert enriched["highlights"][0]["text"] == "פתיחה הרעיון המרכזי"
    assert enriched["topics"] == ["יזמות", "מימון"]
    assert enriched["selection"] == selection


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
