import pytest

from tools.route_optimizer import optimize_route


DEPOT = {"lat": 0, "lon": 0}


def stop(stop_id, demand, need_score, required=False, currently_served=False):
    return {"stop_id": stop_id, "lat": 0, "lon": 0, "demand": demand, "need_score": need_score,
            "population": demand * 2, "required": required, "currently_served": currently_served}


def test_optimizer_selects_highest_benefit_feasible_route():
    candidates = [stop("A", 5, 90), stop("B", 5, 70), stop("C", 8, 40)]
    matrix = [[0, 10, 10, 10], [10, 0, 5, 5], [10, 5, 0, 5], [10, 5, 5, 0]]
    result = optimize_route(candidates, DEPOT, 80, 10, 2, service_minutes=10, travel_time_matrix=matrix)
    assert result["status"] == "optimal"
    assert [row["stop_id"] for row in result["selected_stops"]] == ["A", "B"]
    assert result["capacity_used"] == 10
    assert result["travel_time_source"] == "provided_road_network_matrix"


def test_required_existing_stop_is_preserved_and_losses_are_visible():
    candidates = [stop("existing", 5, 1, required=True, currently_served=True), stop("new", 5, 100)]
    matrix = [[0, 5, 5], [5, 0, 5], [5, 5, 0]]
    result = optimize_route(candidates, DEPOT, 30, 5, 1, service_minutes=5, travel_time_matrix=matrix)
    assert [row["stop_id"] for row in result["selected_stops"]] == ["existing"]
    assert result["coverage_change"]["lost"] == []
    assert result["coverage_change"]["still_uncovered"] == ["new"]


def test_infeasible_required_stops_are_reported():
    result = optimize_route([stop("A", 20, 90, required=True)], DEPOT, 60, 10, 1,
                            travel_time_matrix=[[0, 5], [5, 0]])
    assert result["status"] == "infeasible"
    assert result["selected_stops"] == []


def test_fallback_is_explicitly_labelled_estimate():
    result = optimize_route([stop("A", 5, 50)], DEPOT, 60, 10, 1)
    assert result["travel_time_source"] == "haversine_drive_time_estimate"


def test_invalid_matrix_and_duplicate_ids_fail_closed():
    with pytest.raises(ValueError, match="2x2"):
        optimize_route([stop("A", 5, 50)], DEPOT, 60, 10, 1, travel_time_matrix=[[0]])
    with pytest.raises(ValueError, match="unique"):
        optimize_route([stop("A", 5, 50), stop("A", 5, 40)], DEPOT, 60, 10, 2)
