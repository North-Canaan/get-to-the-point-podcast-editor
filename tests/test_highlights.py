import pytest

from podcast_editor.pipeline.highlights import enrich_highlights, parse_json_response


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

    enriched = enrich_highlights(payload, transcript)

    assert enriched["highlights"][0]["id"] == "h01"
    assert enriched["highlights"][0]["text"] == "פתיחה הרעיון המרכזי"
