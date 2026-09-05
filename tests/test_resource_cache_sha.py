from tools import resource_cache


def test_refresh_publishes_checksum_and_encryption(monkeypatch):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("EVIDENCE_BUCKET", "evidence")
    monkeypatch.setattr(
        resource_cache,
        "get_existing_resources",
        lambda: [{"kind": "grocery", "name": "Market", "lat": 41.8, "lon": -87.7}],
    )
    monkeypatch.setattr(resource_cache.boto3, "client", lambda service: FakeS3())

    resource_cache.refresh_resource_cache("urban")

    assert captured["ServerSideEncryption"] == "AES256"
    assert len(captured["Metadata"]["sha256"]) == 64
    assert captured["Metadata"]["data-classification"] == "public_aggregate"
