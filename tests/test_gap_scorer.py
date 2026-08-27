"""Unit tests for the deterministic scorer — the one piece of this project
that should be fully testable without any AWS credentials, network access,
or LLM call. Run with: pytest
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.gap_scorer import score_gaps  # noqa: E402


def make_tract(fips, population, half=1, one=1, lat=41.80, lon=-87.63):
    return {
        "tract_fips": fips,
        "population": population,
        "low_access_half_mile": half,
        "low_access_one_mile": one,
        "centroid_lat": lat,
        "centroid_lon": lon,
    }


def test_higher_severity_and_no_nearby_resource_ranks_first():
    tracts = [
        make_tract("A", population=3000, half=1, one=1, lat=41.80, lon=-87.63),
        make_tract("B", population=3000, half=0, one=1, lat=41.90, lon=-87.70),
    ]
    # A resource sits right next to tract B, none near tract A.
    resources = [{"kind": "grocery", "name": "Test Grocery", "lat": 41.901, "lon": -87.701}]

    # @tool-decorated functions stay directly callable — no agent needed.
    result = score_gaps(tracts, resources, top_n=2)

    assert result[0]["tract_fips"] == "A"
    assert result[0]["need_score"] > result[1]["need_score"]


def test_top_n_limits_results():
    tracts = [make_tract(str(i), population=1000 + i) for i in range(5)]
    result = score_gaps(tracts, [], top_n=2)
    assert len(result) == 2


def test_empty_resources_does_not_crash_and_maxes_distance_component():
    tracts = [make_tract("A", population=2000)]
    result = score_gaps(tracts, [], top_n=1)
    assert result[0]["nearest_resource_kind"] is None
    assert 0 <= result[0]["need_score"] <= 100


def test_nearby_convenience_store_scores_worse_than_nearby_grocery():
    """A corner store shouldn't count the same as a real grocery store —
    same distance, different kind, should not produce the same need_score."""
    tract = make_tract("A", population=3000)

    near_grocery = score_gaps(
        [tract], [{"kind": "grocery", "name": "G", "lat": 41.801, "lon": -87.631}], top_n=1
    )[0]
    near_convenience = score_gaps(
        [tract], [{"kind": "convenience", "name": "C", "lat": 41.801, "lon": -87.631}], top_n=1
    )[0]

    assert near_convenience["need_score"] > near_grocery["need_score"]
