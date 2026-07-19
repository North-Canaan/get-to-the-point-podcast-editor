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


def test_release_job_only_releases_matching_worker(monkeypatch) -> None:
    client = SupabaseClient("https://example.supabase.co", "sb_secret_test", "artifacts")
    request = {}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def patch(self, url, headers, json):
            request.update(url=url, headers=headers, json=json)
            return httpx.Response(
                200, json=[{"id": "job"}], request=httpx.Request("PATCH", url)
            )

    monkeypatch.setattr("podcast_editor.cloud.httpx.Client", FakeClient)

    released = client.release_job(
        "11111111-1111-4111-8111-111111111111", "web-worker/1"
    )

    assert released is True
    assert request["url"].endswith(
        "?id=eq.11111111-1111-4111-8111-111111111111&worker_id=eq.web-worker%2F1"
    )
    assert request["json"] == {"worker_id": None, "locked_at": None}
