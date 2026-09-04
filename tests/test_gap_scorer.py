"""Unit tests for the deterministic scorer — the one piece of this project
that should be fully testable without any AWS credentials, network access,
or LLM call. Run with: pytest
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools import gap_scorer  # noqa: E402
from tools.gap_scorer import PriorityWeights, score_gaps  # noqa: E402


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


def test_missing_centroid_does_not_crash():
    """A real Atlas download doesn't always ship lat/lon (see
    data/prep_atlas.py's centroid-join fallback) — a tract with no
    centroid must degrade to 'no resource found', not raise."""
    tract = make_tract("A", population=2000)
    tract["centroid_lat"] = None
    tract["centroid_lon"] = None
    resources = [{"kind": "grocery", "name": "G", "lat": 41.80, "lon": -87.63}]

    result = score_gaps([tract], resources, top_n=1)

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


def test_acs_components_are_visible_and_explainable():
    tract = make_tract("A", population=3000)
    tract.update(population_below_poverty=600, poverty_universe=2000,
                 households_no_vehicle=250, households_total=1000)
    result = score_gaps([tract], [], top_n=1)[0]
    assert result["score_components"]["poverty"] == 30.0
    assert result["score_components"]["no_vehicle"] == 25.0
    assert result["missing_components"] == ["transit_burden"]
    assert result["score_explanation"]


def test_food_insecurity_context_replaces_below_poverty_measure():
    tract = make_tract("A", population=3000)
    tract.update(
        food_insecurity_rate=0.80,
        population_below_poverty=100,
        poverty_universe=1000,
        scoring_context_version="food-access-advisor-urban-context-v1",
    )
    weights = {
        "food_access_gap": 0,
        "poverty": 1,
        "no_vehicle": 0,
        "population_served": 0,
        "transit_burden": 0,
        "existing_coverage": 0,
    }

    result = score_gaps([tract], [], top_n=1, weights=weights)[0]

    assert result["score_components"]["poverty"] == 80.0
    assert "food-insecurity risk" in result["score_explanation"]


def test_prepared_transportation_is_a_distinct_scored_component():
    high_burden = make_tract("A", population=1000)
    high_burden.update(transit_burden=0.9, households_no_vehicle=10, households_total=100)
    low_burden = make_tract("B", population=1000)
    low_burden.update(transit_burden=0.1, households_no_vehicle=10, households_total=100)
    weights = {
        "food_access_gap": 0,
        "poverty": 0,
        "no_vehicle": 0,
        "population_served": 0,
        "transit_burden": 1,
        "existing_coverage": 0,
    }

    result = score_gaps([low_burden, high_burden], [], top_n=2, weights=weights)

    assert [row["tract_fips"] for row in result] == ["A", "B"]
    assert result[0]["score_components"]["transit_burden"] == 90.0
    assert "transit_burden" not in result[0]["missing_components"]


def test_urban_coverage_distinguishes_garden_from_full_grocery():
    tract = make_tract("A", population=3000)
    tract["scoring_context_version"] = "food-access-advisor-urban-context-v1"
    weights = {
        "food_access_gap": 1,
        "poverty": 0,
        "no_vehicle": 0,
        "population_served": 0,
        "transit_burden": 0,
        "existing_coverage": 1,
    }
    garden = score_gaps(
        [tract],
        [{"kind": "garden", "name": "G", "lat": 41.801, "lon": -87.631}],
        top_n=1,
        weights=weights,
    )[0]
    grocery = score_gaps(
        [tract],
        [{"kind": "grocery", "name": "M", "lat": 41.801, "lon": -87.631}],
        top_n=1,
        weights=weights,
    )[0]

    assert garden["need_score"] > grocery["need_score"]


def test_closer_garden_does_not_hide_nearby_grocery_coverage():
    tract = make_tract("A", population=3000)
    tract["scoring_context_version"] = "food-access-advisor-urban-context-v1"
    weights = {
        "food_access_gap": 1,
        "poverty": 0,
        "no_vehicle": 0,
        "population_served": 0,
        "transit_burden": 0,
        "existing_coverage": 1,
    }
    grocery = {"kind": "grocery", "name": "M", "lat": 41.802, "lon": -87.632}
    garden = {"kind": "garden", "name": "G", "lat": 41.801, "lon": -87.631}

    grocery_only = score_gaps([tract], [grocery], top_n=1, weights=weights)[0]
    both = score_gaps([tract], [garden, grocery], top_n=1, weights=weights)[0]

    assert both["nearest_resource_kind"] == "garden"
    assert both["need_score"] == grocery_only["need_score"]


def test_adjustable_weights_can_change_ranking():
    high_poverty = make_tract("A", population=1000, half=0, one=1)
    high_poverty["poverty_rate"] = 0.8
    high_population = make_tract("B", population=5000, half=0, one=1)
    high_population["poverty_rate"] = 0.1
    weights = {"food_access_gap": 0, "poverty": 1, "no_vehicle": 0,
               "population_served": 0, "transit_burden": 0, "existing_coverage": 0}
    result = score_gaps([high_population, high_poverty], [], top_n=2, weights=weights)
    assert result[0]["tract_fips"] == "A"
    assert "poverty" in result[0]["score_explanation"]
    assert "food-insecurity risk" not in result[0]["score_explanation"]
    assert "food-access gap" not in result[0]["score_explanation"]


def test_weights_are_normalized_and_invalid_weights_fail():
    assert sum(PriorityWeights(poverty=2).normalized().values()) == 1
    try:
        score_gaps([make_tract("A", 1)], [], weights={"poverty": -1})
    except ValueError as error:
        assert "negative" in str(error)
    else:
        raise AssertionError("negative weights must fail")


def test_sensitivity_range_contains_baseline_score():
    result = score_gaps([make_tract("A", 2000)], [], top_n=1)[0]
    assert result["sensitivity"]["score_min"] <= result["need_score"] <= result["sensitivity"]["score_max"]
    assert result["sensitivity"]["rank_stable"] is True


def test_sensitivity_reuses_weight_independent_resource_evidence(monkeypatch):
    tracts = [make_tract("A", 2000), make_tract("B", 1000)]
    resources = [{"kind": "grocery", "name": "G", "lat": 41.81, "lon": -87.64}]
    calls = {"nearest": 0, "coverage": 0}
    original_nearest = gap_scorer._nearest_resource
    original_coverage = gap_scorer._best_coverage

    def counted_nearest(*args, **kwargs):
        calls["nearest"] += 1
        return original_nearest(*args, **kwargs)

    def counted_coverage(*args, **kwargs):
        calls["coverage"] += 1
        return original_coverage(*args, **kwargs)

    monkeypatch.setattr(gap_scorer, "_nearest_resource", counted_nearest)
    monkeypatch.setattr(gap_scorer, "_best_coverage", counted_coverage)

    score_gaps(tracts, resources, top_n=2)

    assert calls == {"nearest": len(tracts), "coverage": len(tracts)}


def test_continuous_rural_gap_overrides_binary_severity():
    high_gap = make_tract("A", population=1000, half=0, one=0)
    high_gap["low_income_low_access_share"] = 0.60
    lower_gap = make_tract("B", population=1000, half=1, one=1)
    lower_gap["low_income_low_access_share"] = 0.19
    weights = {
        "food_access_gap": 1,
        "poverty": 0,
        "no_vehicle": 0,
        "population_served": 0,
        "transit_burden": 0,
        "existing_coverage": 0,
    }

    result = score_gaps([lower_gap, high_gap], [], top_n=2, weights=weights)

    assert [row["tract_fips"] for row in result] == ["A", "B"]
    assert result[0]["score_components"]["food_access_gap"] == 60.0
    assert result[1]["score_components"]["food_access_gap"] == 19.0
