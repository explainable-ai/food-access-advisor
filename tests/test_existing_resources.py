"""Unit tests for the Overpass retry/failure behavior — mocks the network
call entirely (no live Overpass dependency in CI), so these run offline
and fast like the rest of the suite.
"""

import inspect
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.existing_resources as existing_resources  # noqa: E402
from tools.existing_resources import (  # noqa: E402
    OverpassQueryError,
    get_existing_resources,
    get_rural_existing_resources,
)


class _FakeResponse:
    def __init__(self, elements):
        self._elements = elements

    def raise_for_status(self):
        pass

    def json(self):
        return {"elements": self._elements}


def test_successful_query_parses_elements(monkeypatch):
    elements = [
        {"type": "node", "id": 10, "lat": 41.80, "lon": -87.63, "tags": {"shop": "supermarket", "name": "Big Grocery"}},
        {"type": "node", "id": 11, "lat": 41.81, "lon": -87.64, "tags": {"shop": "convenience", "name": "Corner Shop"}},
        {"type": "way", "id": 12, "center": {"lat": 41.82, "lon": -87.65}, "tags": {"leisure": "garden", "name": "Community Garden"}},
    ]
    monkeypatch.setattr(
        existing_resources.requests, "post", lambda *a, **k: _FakeResponse(elements)
    )

    result = get_existing_resources()

    kinds = {r["name"]: r["kind"] for r in result}
    assert kinds["Big Grocery"] == "grocery"
    assert kinds["Corner Shop"] == "convenience"
    assert kinds["Community Garden"] == "garden"
    assert result[0]["entity_id"] == "osm:node/10"


def test_transient_failure_then_success_recovers(monkeypatch):
    """A single blip shouldn't take down the whole call — that's the point
    of retrying before giving up."""
    calls = {"n": 0}

    def flaky_post(*a, **k):
        calls["n"] += 1
        if calls["n"] < existing_resources.MAX_ATTEMPTS:
            raise requests.exceptions.ConnectionError("transient blip")
        return _FakeResponse([])

    monkeypatch.setattr(existing_resources.requests, "post", flaky_post)
    monkeypatch.setattr(existing_resources.time, "sleep", lambda *_: None)

    result = get_existing_resources()

    assert result == []
    assert calls["n"] == existing_resources.MAX_ATTEMPTS


def test_persistent_failure_raises_instead_of_silently_returning_empty(monkeypatch):
    """The bug this replaces: a persistent outage used to come back as an
    empty list indistinguishable from 'confirmed zero resources nearby'.
    It must now raise, not swallow."""

    def always_fails(*a, **k):
        raise requests.exceptions.Timeout("Overpass is down")

    monkeypatch.setattr(existing_resources.requests, "post", always_fails)
    monkeypatch.setattr(existing_resources.time, "sleep", lambda *_: None)

    with pytest.raises(OverpassQueryError):
        get_existing_resources()


def test_get_existing_resources_has_no_region_argument():
    """Same boundary discipline as the rest of this project's tools."""
    sig = inspect.signature(get_existing_resources)
    assert len(sig.parameters) == 0


def test_get_rural_existing_resources_has_no_region_argument():
    """Same boundary discipline, rural side."""
    sig = inspect.signature(get_rural_existing_resources)
    assert len(sig.parameters) == 0


def test_a_real_user_agent_is_sent(monkeypatch):
    """Regression test for the 406 Overpass returned to requests' generic
    default User-Agent — a real project identifier must be sent instead."""
    captured = {}

    def capturing_post(url, data=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse([])

    monkeypatch.setattr(existing_resources.requests, "post", capturing_post)

    get_existing_resources()

    assert captured["headers"]["User-Agent"].startswith("food-access-advisor")


def test_rural_query_includes_food_bank_and_marketplace_tags(monkeypatch):
    """The rural snapshot retains both resource categories."""
    captured = {}

    def capturing_post(url, data=None, headers=None, timeout=None):
        captured["query"] = data["data"]
        return _FakeResponse([])

    monkeypatch.setattr(existing_resources.requests, "post", capturing_post)

    get_rural_existing_resources()

    assert "social_facility" in captured["query"] and "food_bank" in captured["query"]
    assert "amenity" in captured["query"] and "marketplace" in captured["query"]


def test_urban_query_includes_food_bank_and_marketplace_tags(monkeypatch):
    captured = {}

    def capturing_post(url, data=None, headers=None, timeout=None):
        captured["query"] = data["data"]
        return _FakeResponse([])

    monkeypatch.setattr(existing_resources.requests, "post", capturing_post)

    get_existing_resources()

    assert "social_facility" in captured["query"] and "food_bank" in captured["query"]
    assert "amenity" in captured["query"] and "marketplace" in captured["query"]


def test_urban_kind_classification(monkeypatch):
    elements = [
        {"type": "node", "id": 30, "lat": 41.80, "lon": -87.63, "tags": {"social_facility": "food_bank", "name": "Chicago Food Bank"}},
        {"type": "node", "id": 31, "lat": 41.81, "lon": -87.64, "tags": {"amenity": "marketplace", "name": "Chicago Market"}},
    ]
    monkeypatch.setattr(
        existing_resources.requests, "post", lambda *a, **k: _FakeResponse(elements)
    )

    result = get_existing_resources()

    kinds = {r["name"]: r["kind"] for r in result}
    assert kinds == {
        "Chicago Food Bank": "food_bank",
        "Chicago Market": "market",
    }


def test_rural_kind_classification(monkeypatch):
    elements = [
        {"type": "node", "id": 20, "lat": 37.01, "lon": -89.18, "tags": {"social_facility": "food_bank", "name": "Rural Food Bank"}},
        {"type": "node", "id": 21, "lat": 37.02, "lon": -89.19, "tags": {"amenity": "marketplace", "name": "Mobile Market"}},
    ]
    monkeypatch.setattr(
        existing_resources.requests, "post", lambda *a, **k: _FakeResponse(elements)
    )

    result = get_rural_existing_resources()

    kinds = {r["name"]: r["kind"] for r in result}
    assert kinds["Rural Food Bank"] == "food_bank"
    assert kinds["Mobile Market"] == "market"


def test_rural_queries_each_planning_band_and_deduplicates(monkeypatch):
    calls = {"count": 0}
    shared = {
        "type": "node",
        "id": 99,
        "lat": 41.64,
        "lon": -88.45,
        "tags": {"shop": "grocery", "name": "Shared Market"},
    }

    def same_resource_for_each_band(*args, **kwargs):
        calls["count"] += 1
        return _FakeResponse([shared])

    monkeypatch.setattr(existing_resources.requests, "post", same_resource_for_each_band)

    result = get_rural_existing_resources()

    assert calls["count"] == len(
        existing_resources.PILOT_RURAL_COUNTY["resource_areas"]
    )
    assert [row["entity_id"] for row in result] == ["osm:node/99"]
