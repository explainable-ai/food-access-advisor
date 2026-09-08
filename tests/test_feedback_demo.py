import services.feedback_demo as feedback


def test_feedback_uses_real_recompute_without_persisting(monkeypatch):
    monkeypatch.setattr(feedback, "_current_scored_tract", lambda tract_id: {
        "tract_fips": tract_id,
        "need_score": 75.0,
        "households_total": 1000,
        "score_components": {"food_access_gap": 100.0, "poverty": 50.0, "no_vehicle": 50.0, "population_served": 50.0, "transit_burden": 50.0, "existing_coverage": 10.0},
        "score_contributions": {},
        "contributions": [],
        "weights_used": {},
    })
    result = feedback.calculate_feedback("17031010100", 100)
    assert result["persisted"] is False
    assert result["households_served"] == 100
    assert result["after"]["score_components"]["existing_coverage"] == 19.0
    assert "uncovered share" in result["method"]


def test_zero_households_preserves_exact_baseline(monkeypatch):
    monkeypatch.setattr(feedback, "_current_scored_tract", lambda tract_id: {
        "tract_fips": tract_id,
        "need_score": 74.95,
        "households_total": 1000,
        "score_components": {"food_access_gap": 99.9, "existing_coverage": 10.0},
        "score_contributions": {"food_access_gap": 74.95},
        "contributions": [],
        "weights_used": {},
    })

    result = feedback.calculate_feedback("17031010100", 0)

    assert result["after"]["score"] == result["before"]["score"] == 74.95
    assert result["after"]["score_components"] == result["before"]["score_components"]
