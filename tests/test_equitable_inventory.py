from services.load_recommendation import (
    assess_inventory_feasibility,
    build_load_recommendation,
)
from route_advisor import run_route_advisor


def _stop(stop_id, need_score, households):
    return {
        "stop_id": stop_id,
        "tract_fips": stop_id,
        "lat": 41.8,
        "lon": -87.6,
        "need_score": need_score,
        "households_total": households,
        "population": households * 2,
        "centroid_lat": 41.8,
        "centroid_lon": -87.6,
    }


def test_inventory_feasibility_caps_requested_load():
    inventory = [
        {
            "item_id": "produce",
            "item": "Fresh Produce Box",
            "qty": 5,
            "unit_weight_lbs": 10,
            "category": "produce",
        }
    ]

    result = assess_inventory_feasibility(inventory, 100)

    assert result["status"] == "partial"
    assert result["available_weight_lbs"] == 50
    assert result["effective_load_lbs"] == 50


def test_expired_inventory_does_not_count_toward_route_capacity():
    inventory = [
        {
            "item_id": "expired",
            "item": "Expired Greens",
            "qty": 100,
            "unit_weight_lbs": 1,
            "days_to_spoil": -1,
        },
        {
            "item_id": "fresh",
            "item": "Fresh Greens",
            "qty": 10,
            "unit_weight_lbs": 1,
            "days_to_spoil": 3,
        },
    ]

    result = assess_inventory_feasibility(inventory, 50)

    assert result["available_weight_lbs"] == 10
    assert result["effective_load_lbs"] == 10


def test_spoilage_window_creates_human_reviewable_rescue_recommendation():
    route = {
        "selected_stops": [
            {"stop_id": "A", "need_score": 70, "households": 100},
            {"stop_id": "B", "need_score": 90, "households": 100},
        ]
    }
    inventory = [
        {
            "item_id": "spinach",
            "item": "Spinach",
            "qty": 20,
            "unit_weight_lbs": 1,
            "category": "produce",
            "nutritional_category": "fresh_produce",
            "days_to_spoil": 2,
        }
    ]

    result = build_load_recommendation(route, inventory, 10)

    assert result["items"][0]["spoilage_status"] == "rescue"
    assert result["items"][0]["distribution_mode"] == "rescue_review"
    assert result["rescue_recommendations"][0]["human_approval_required"] is True


def test_equity_allocation_protects_later_high_need_stop():
    route = {
        "selected_stops": [
            {"stop_id": "A", "sequence": 1, "need_score": 50, "households": 100},
            {"stop_id": "B", "sequence": 2, "need_score": 95, "households": 100},
        ]
    }
    inventory = [
        {
            "item_id": "beans",
            "item": "Bean Bag",
            "qty": 10,
            "unit_weight_lbs": 1,
            "category": "protein",
        }
    ]

    result = build_load_recommendation(route, inventory, 10)
    allocations = {
        row["stop_id"]: row["qty"]
        for row in result["items"][0]["allocations"]
    }

    assert allocations["A"] > 0
    assert allocations["B"] > 0
    assert allocations["B"] >= allocations["A"]
    assert result["all_stops_protected"] is True


def test_router_caps_demand_by_inventory_before_optimization():
    class InventoryStore:
        def read(self, key):
            return [
                {
                    "item_id": "boxes",
                    "item": "Food Box",
                    "qty": 5,
                    "unit_weight_lbs": 10,
                }
            ]

    tracts = [
        {
            "tract_fips": "17031010100",
            "centroid_lat": 41.80,
            "centroid_lon": -87.60,
            "need_score": 90,
            "households_total": 100,
            "population": 200,
        },
        {
            "tract_fips": "17031010200",
            "centroid_lat": 41.81,
            "centroid_lon": -87.61,
            "need_score": 80,
            "households_total": 100,
            "population": 200,
        },
    ]

    result = run_route_advisor(
        tracts,
        {"lat": 41.79, "lon": -87.62},
        4,
        200,
        matrix_fn=lambda points: [
            [0 if i == j else 5 for j in range(len(points))]
            for i in range(len(points))
        ],
        inventory_store=InventoryStore(),
    )

    assert result["status"] == "optimal"
    assert result["requested_load_lbs"] == 200
    assert result["effective_route_load_lbs"] == 50
    assert result["capacity_used"] <= 50
