"""Tests for the shared Chicago Site Advisor ranking path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import site_ranker  # noqa: E402


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
