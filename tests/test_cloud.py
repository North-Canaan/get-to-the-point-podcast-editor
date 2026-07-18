from podcast_editor.cloud import SupabaseClient


def test_modern_supabase_secret_is_not_sent_as_bearer_token() -> None:
    client = SupabaseClient("https://example.supabase.co", "sb_secret_test", "artifacts")

    assert client.headers == {"apikey": "sb_secret_test"}


def test_legacy_service_role_jwt_is_sent_as_bearer_token() -> None:
    client = SupabaseClient("https://example.supabase.co", "eyJlegacy", "artifacts")

    assert client.headers == {
        "apikey": "eyJlegacy",
        "Authorization": "Bearer eyJlegacy",
    }
