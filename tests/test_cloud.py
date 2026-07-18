import httpx

from podcast_editor.cloud import SupabaseClient, storage_object_not_found


def test_modern_supabase_secret_is_not_sent_as_bearer_token() -> None:
    client = SupabaseClient("https://example.supabase.co", "sb_secret_test", "artifacts")

    assert client.headers == {"apikey": "sb_secret_test"}


def test_legacy_service_role_jwt_is_sent_as_bearer_token() -> None:
    client = SupabaseClient("https://example.supabase.co", "eyJlegacy", "artifacts")

    assert client.headers == {
        "apikey": "eyJlegacy",
        "Authorization": "Bearer eyJlegacy",
    }


def test_supabase_storage_400_object_missing_is_not_found() -> None:
    response = httpx.Response(
        400,
        json={"statusCode": "404", "error": "not_found", "message": "Object not found"},
    )

    assert storage_object_not_found(response) is True


def test_unrelated_storage_400_is_not_swallowed() -> None:
    response = httpx.Response(400, json={"message": "invalid request"})

    assert storage_object_not_found(response) is False
