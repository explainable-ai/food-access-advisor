"""Unit tests for orchestration.py -- no real Bedrock call: build_advisor/
build_route_advisor/build_watchdog are mocked with a fake Agent-like object
so graph construction and dispatch are tested without model.py's
build_model() ever running (same no-network-in-tests philosophy as the
rest of this repo).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import orchestration  # noqa: E402


class FakeAgent:
    """Minimal stand-in for a Strands Agent -- just enough surface for
    GraphBuilder.add_node (an AgentBase instance) and for being called."""

    def __call__(self, prompt):
        return f"fake response to: {prompt}"


def test_route_request_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unknown mode"):
        orchestration.route_request("recheck", "does this exist?")


def test_build_site_graph_constructs_with_one_node(monkeypatch):
    monkeypatch.setattr(orchestration, "build_advisor", lambda: FakeAgent())

    graph = orchestration.build_site_graph()

    assert graph is not None


def test_build_route_graph_constructs_with_one_node(monkeypatch):
    monkeypatch.setattr(orchestration, "build_route_advisor", lambda: FakeAgent())

    graph = orchestration.build_route_graph()

    assert graph is not None


def test_build_watchdog_graph_constructs_with_one_node(monkeypatch):
    monkeypatch.setattr(orchestration, "build_watchdog", lambda: FakeAgent())

    graph = orchestration.build_watchdog_graph()

    assert graph is not None


def test_route_request_site_mode_builds_site_graph_not_route_graph(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestration, "build_site_graph", lambda: calls.append("site") or (lambda q: "site result"))
    monkeypatch.setattr(orchestration, "build_route_graph", lambda: calls.append("route") or (lambda q: "route result"))

    result = orchestration.route_request("site", "where's the highest-need spot?")

    assert calls == ["site"]
    assert result == "site result"


def test_route_request_route_mode_builds_route_graph_not_site_graph(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestration, "build_site_graph", lambda: calls.append("site") or (lambda q: "site result"))
    monkeypatch.setattr(orchestration, "build_route_graph", lambda: calls.append("route") or (lambda q: "route result"))

    result = orchestration.route_request("route", "where would a route change help?")

    assert calls == ["route"]
    assert result == "route result"
