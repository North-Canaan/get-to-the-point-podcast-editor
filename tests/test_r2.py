from pathlib import Path

from podcast_editor.r2 import R2Client


class Body:
    def __init__(self) -> None:
        self.closed = False

    def read(self, _size: int) -> bytes:
        return b"ID3"

    def close(self) -> None:
        self.closed = True


class FakeS3:
    def __init__(self) -> None:
        self.calls = []
        self.body = Body()

    def upload_file(self, *args, **kwargs) -> None:
        self.calls.append(("upload", args, kwargs))

    def head_object(self, **kwargs):
        return {"ContentLength": 3, "ContentType": "audio/mpeg"}

    def get_object(self, **kwargs):
        return {"Body": self.body}

    def copy_object(self, **kwargs) -> None:
        self.calls.append(("copy", kwargs))

    def delete_object(self, **kwargs) -> None:
        self.calls.append(("delete", kwargs))

    def generate_presigned_url(self, method, Params, ExpiresIn):
        return f"https://signed.test/{method}/{Params['Key']}?expires={ExpiresIn}"


def test_r2_upload_verify_promote_and_sign(tmp_path: Path) -> None:
    path = tmp_path / "output.mp3"
    path.write_bytes(b"ID3")
    fake = FakeS3()
    client = R2Client(fake, "bucket")
    client.upload("temporary/a.mp3", path)
    client.verify("temporary/a.mp3", 3)
    client.promote("temporary/a.mp3", "deliveries/a.mp3")
    assert fake.body.closed
    assert client.signed_url("deliveries/a.mp3", "head_object").startswith(
        "https://signed.test/head_object/"
    )
