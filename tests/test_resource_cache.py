import json

import pytest

from tools import resource_cache


def test_loads_local_prepared_cache(tmp_path, monkeypatch):
    (tmp_path / "rural.json").write_text(json.dumps({"resources": [
        {"kind": "grocery", "name": "Market", "lat": 37.1, "lon": -89.2}
    ]}))
    monkeypatch.delenv("RESOURCE_CACHE_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)
    monkeypatch.setenv("RESOURCE_CACHE_DIR", str(tmp_path))
    assert resource_cache.load_resource_cache("rural")[0]["name"] == "Market"


def test_missing_cache_is_not_silently_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("RESOURCE_CACHE_BUCKET", raising=False)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)
    monkeypatch.setenv("RESOURCE_CACHE_DIR", str(tmp_path))
    with pytest.raises(resource_cache.ResourceCacheError):
        resource_cache.load_resource_cache("urban")
