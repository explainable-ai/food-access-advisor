"""Shared pytest defaults for local/CI test isolation."""

import pytest


@pytest.fixture(autouse=True)
def default_local_test_auth(monkeypatch):
    """Keep API tests independent from a developer's production-like .env.

    Production and deployment config still set FOOD_ACCESS_AUTH_MODE=required.
    Unit/API tests use disabled auth by default so staff-only endpoints can be
    exercised with mocked business logic unless a test explicitly overrides the
    mode.
    """
    monkeypatch.setenv("FOOD_ACCESS_AUTH_MODE", "disabled")
