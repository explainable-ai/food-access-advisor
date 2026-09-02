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


def test_rural_complete_coverage_requires_every_planning_band():
    first = resource_cache.PILOT_RURAL_COUNTY["resource_areas"][0]["bbox"]
    payload = {"coverage_bboxes": [first], "resources": [{"kind": "grocery", "lat": 41, "lon": -88}]}

    with pytest.raises(resource_cache.ResourceCacheError, match="does not cover"):
        resource_cache._validate_coverage("rural", payload)


def test_rural_complete_coverage_accepts_all_planning_bands():
    payload = {
        "coverage_bboxes": [
            area["bbox"] for area in resource_cache.PILOT_RURAL_COUNTY["resource_areas"]
        ],
        "resources": [{"kind": "grocery", "lat": 41, "lon": -88}],
    }

    resource_cache._validate_coverage("rural", payload)
