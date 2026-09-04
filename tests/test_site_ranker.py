"""Tests for the shared Chicago Site Advisor ranking path."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import site_ranker  # noqa: E402
from tools.access_data import PreparedTractDataError  # noqa: E402


def test_ranker_scores_full_chicago_universe_before_top_n(monkeypatch):
    tracts = [
        {"tract_fips": str(index), "is_chicago": index < 30}
        for index in range(35)
    ]
    captured = {}
    monkeypatch.setattr(site_ranker, "get_all_tracts", lambda: tracts)
    monkeypatch.setattr(
        site_ranker,
        "load_resource_cache",
        lambda scope, require_complete_coverage=False: ["prepared-resource"],
    )

    def fake_score(candidates, resources, top_n):
        captured.update(
            candidates=candidates,
            resources=resources,
            top_n=top_n,
        )
        return candidates[:top_n]

    monkeypatch.setattr(site_ranker, "score_gaps", fake_score)

    result = site_ranker.rank_chicago_tracts(top_n=5)

    assert len(captured["candidates"]) == 30
    assert captured["resources"] == ["prepared-resource"]
    assert captured["top_n"] == 5
    assert len(result) == 5


def test_ranker_retains_fresh_checkout_sample_fallback(monkeypatch):
    sample_tracts = [
        {"tract_fips": "sample-1", "data_mode": "sample"},
        {"tract_fips": "sample-2", "data_mode": "sample"},
    ]
    monkeypatch.setattr(
        site_ranker,
        "get_all_tracts",
        lambda: (_ for _ in ()).throw(PreparedTractDataError("not prepared")),
    )
    monkeypatch.setattr(
        site_ranker, "get_low_access_tracts", lambda limit: sample_tracts[:limit]
    )
    monkeypatch.setattr(
        site_ranker, "get_existing_resources", lambda: ["live-resource"]
    )
    monkeypatch.setattr(
        site_ranker,
        "load_resource_cache",
        lambda *args, **kwargs: pytest.fail("sample mode must not require a cache"),
    )
    monkeypatch.setattr(
        site_ranker,
        "score_gaps",
        lambda tracts, resources, top_n: {
            "tracts": tracts,
            "resources": resources,
            "top_n": top_n,
        },
    )

    assert site_ranker.rank_chicago_tracts(top_n=1) == {
        "tracts": sample_tracts[:1],
        "resources": ["live-resource"],
        "top_n": 1,
    }


def test_ranker_does_not_mask_incomplete_prepared_data(monkeypatch):
    prepared_error = PreparedTractDataError("prepared data is incomplete")
    monkeypatch.setattr(
        site_ranker,
        "get_all_tracts",
        lambda: (_ for _ in ()).throw(prepared_error),
    )
    monkeypatch.setattr(
        site_ranker,
        "get_low_access_tracts",
        lambda limit: [{"tract_fips": "real-1", "data_mode": "real"}],
    )

    with pytest.raises(PreparedTractDataError, match="prepared data is incomplete"):
        site_ranker.rank_chicago_tracts(top_n=1)
