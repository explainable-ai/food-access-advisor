import pytest

from services.equity_allocator import objective_weights
from services.load_recommendation import (
    assess_inventory_feasibility,
    build_load_recommendation,
)


def _allocation_for(result, item_id):
    item = next(item for item in result["items"] if item["item_id"] == item_id)
    return {row["stop_id"]: row["qty"] for row in item["allocations"]}


def test_dispatch_exposes_knapsack_of_equity_objective():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {"stop_id": "A", "need_score": 80, "households": 100, "demand": 20},
            ]
        },
        [
            {
                "item_id": "beans",
                "item": "Beans",
                "qty": 20,
                "unit_weight_lbs": 1,
                "category": "protein",
                "nutritional_category": "protein",
                "days_to_spoil": 3,
            }
        ],
        20,
    )

    objective = result["dispatch_objective"]
    assert objective["name"] == "knapsack_of_equity_v1"
    assert "Q[i,s]" in objective["formula"]
    assert objective["allocated_units"] == 20
    assert objective["total_score"] > 0
    assert sum(objective["weights"].values()) == pytest.approx(1.0)
    assert objective["human_review_required"] is True


def test_objective_sends_remaining_units_to_higher_vulnerability_stop():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {"stop_id": "A", "sequence": 1, "need_score": 20, "households": 100, "demand": 10},
                {"stop_id": "B", "sequence": 2, "need_score": 95, "households": 100, "demand": 10},
            ]
        },
        [
            {
                "item_id": "beans",
                "item": "Bean Bag",
                "qty": 10,
                "unit_weight_lbs": 1,
                "category": "protein",
            }
        ],
        10,
    )

    allocations = _allocation_for(result, "beans")
    assert allocations["A"] >= 1
    assert allocations["B"] >= 1
    assert allocations["B"] > allocations["A"]
    assert result["all_stops_protected"] is True


def test_scarce_skus_are_coordinated_to_protect_every_stop_first():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {"stop_id": "A", "sequence": 1, "need_score": 95, "households": 100, "demand": 1},
                {"stop_id": "B", "sequence": 2, "need_score": 60, "households": 100, "demand": 1},
            ]
        },
        [
            {"item_id": "one", "item": "Item One", "qty": 1, "unit_weight_lbs": 1},
            {"item_id": "two", "item": "Item Two", "qty": 1, "unit_weight_lbs": 1},
        ],
        2,
    )

    stop_reserves = {row["stop_id"]: row for row in result["stop_reserves"]}
    assert stop_reserves["A"]["reserve_protected"] is True
    assert stop_reserves["B"]["reserve_protected"] is True
    assert stop_reserves["A"]["reserved_weight_lbs"] == 1
    assert stop_reserves["B"]["reserved_weight_lbs"] == 1
    assert result["all_stops_protected"] is True


def test_explicit_stop_nutrition_requests_drive_item_stop_match():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {
                    "stop_id": "produce-stop",
                    "need_score": 70,
                    "households": 100,
                    "demand": 6,
                    "nutritional_priorities": ["produce"],
                },
                {
                    "stop_id": "protein-stop",
                    "need_score": 70,
                    "households": 100,
                    "demand": 6,
                    "nutritional_priorities": ["protein"],
                },
            ]
        },
        [
            {
                "item_id": "greens",
                "item": "Fresh Greens",
                "qty": 6,
                "unit_weight_lbs": 1,
                "category": "produce",
                "nutritional_category": "fresh_produce",
            },
            {
                "item_id": "beans",
                "item": "Beans",
                "qty": 6,
                "unit_weight_lbs": 1,
                "category": "protein",
                "nutritional_category": "protein",
            },
        ],
        12,
    )

    greens = _allocation_for(result, "greens")
    beans = _allocation_for(result, "beans")
    assert greens.get("produce-stop", 0) > greens.get("protein-stop", 0)
    assert beans.get("protein-stop", 0) > beans.get("produce-stop", 0)

    nutrition_contribution = result["dispatch_objective"]["weighted_component_totals"]["nutrition"]
    assert nutrition_contribution > 0


def test_no_demographic_preference_is_inferred_when_stop_has_no_request():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {
                    "stop_id": "A",
                    "need_score": 90,
                    "households": 100,
                    "demand": 2,
                    "community_area": "Example",
                }
            ]
        },
        [
            {
                "item_id": "rice",
                "item": "Rice",
                "qty": 2,
                "unit_weight_lbs": 1,
                "category": "pantry",
            }
        ],
        2,
    )

    allocation = result["items"][0]["allocations"][0]
    assert allocation["objective"]["nutrition_match_basis"] == "neutral_no_explicit_preference_data"


def test_minimum_warehouse_reserve_is_not_offered_to_route():
    inventory = [
        {
            "item_id": "milk",
            "item": "Milk",
            "qty": 10,
            "minimum_reserve": 4,
            "unit_weight_lbs": 2,
            "category": "dairy",
        }
    ]

    result = assess_inventory_feasibility(inventory, 30)

    assert result["available_weight_lbs"] == 12
    assert result["effective_load_lbs"] == 12


def test_objective_enforces_max_allocation_per_household():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {"stop_id": "A", "need_score": 95, "households": 2, "demand": 10},
                {"stop_id": "B", "need_score": 60, "households": 8, "demand": 10},
            ]
        },
        [
            {
                "item_id": "boxes",
                "item": "Food Box",
                "qty": 10,
                "unit_weight_lbs": 1,
                "category": "pantry",
                "max_allocation_per_household": 1,
            }
        ],
        10,
    )

    allocations = _allocation_for(result, "boxes")
    assert allocations["A"] <= 2
    assert allocations["B"] <= 8
    assert sum(allocations.values()) == 10


def test_single_stop_legacy_demand_does_not_truncate_selected_load():
    result = build_load_recommendation(
        {
            "selected_stops": [
                {"stop_id": "A", "need_score": 70, "demand": 40},
            ]
        },
        [
            {
                "item_id": "apples",
                "item": "Apples",
                "qty": 100,
                "unit_weight_lbs": 1,
            }
        ],
        200,
    )

    assert result["recommended_weight_lbs"] == 100


def test_objective_weights_are_runtime_policy_parameters(monkeypatch):
    monkeypatch.setenv("DISPATCH_OBJECTIVE_VULNERABILITY_WEIGHT", "2")
    monkeypatch.setenv("DISPATCH_OBJECTIVE_NUTRITION_WEIGHT", "1")
    monkeypatch.setenv("DISPATCH_OBJECTIVE_SPOILAGE_WEIGHT", "1")

    weights = objective_weights()

    assert weights == pytest.approx(
        {"vulnerability": 0.5, "nutrition": 0.25, "spoilage": 0.25}
    )
