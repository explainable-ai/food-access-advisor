"""Unit tests for the Overpass retry/failure behavior — mocks the network
call entirely (no live Overpass dependency in CI), so these run offline
and fast like the rest of the suite.
"""

import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.existing_resources as existing_resources  # noqa: E402
from tools.existing_resources import OverpassQueryError, get_existing_resources  # noqa: E402


class _FakeResponse:
    def __init__(self, elements):
        self._elements = elements

    def raise_for_status(self):
        pass

    def json(self):
        return {"elements": self._elements}


def test_successful_query_parses_elements(monkeypatch):
    elements = [
        {"lat": 41.80, "lon": -87.63, "tags": {"shop": "supermarket", "name": "Big Grocery"}},
        {"lat": 41.81, "lon": -87.64, "tags": {"shop": "convenience", "name": "Corner Shop"}},
        {"center": {"lat": 41.82, "lon": -87.65}, "tags": {"leisure": "garden", "name": "Community Garden"}},
    ]
    monkeypatch.setattr(
        existing_resources.requests, "post", lambda *a, **k: _FakeResponse(elements)
    )

    result = get_existing_resources()

    kinds = {r["name"]: r["kind"] for r in result}
    assert kinds["Big Grocery"] == "grocery"
    assert kinds["Corner Shop"] == "convenience"
    assert kinds["Community Garden"] == "garden"


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
    import inspect

    sig = inspect.signature(get_existing_resources)
    assert len(sig.parameters) == 0
