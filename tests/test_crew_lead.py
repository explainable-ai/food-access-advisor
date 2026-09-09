import crew_lead
import agent as scout_agent
from services.load_recommendation import build_load_recommendation
from services.mission_preview import run_mission_ops
from storage.inventory import COLD_CHAIN_KEY, ON_HAND_KEY


class FakeCrewAgent:
    def __init__(self, area="chicago_neighborhoods"):
        self.area = area

    def __call__(self, prompt):
        crew_lead.sentry_check(self.area)
        crew_lead.scout(self.area, prompt, area_was_inferred=False)
        crew_lead.router([], {}, 4, 200)
        crew_lead.dispatch({}, 200, 4)


def _successful_dependencies(monkeypatch):
    monkeypatch.setattr(crew_lead, "run_watchdog", lambda area: {"status": "complete", "checked": 0})
    monkeypatch.setattr(crew_lead, "run_site_advisor", lambda area, scenario: {"study_area": area, "ranked_count": 12, "top_tracts": [{"tract_fips": "17031010100"}]})
    monkeypatch.setattr(crew_lead, "run_route_advisor", lambda tracts, hub, hours, load: {"status": "optimal", "selected_stops": [{"stop_id": "17031010100"}], "route_minutes": 180, "capacity_used": 200})
    monkeypatch.setattr(crew_lead, "run_mission_ops", lambda route, load, hours, **kwargs: {"mission_id": "mission-test", "status": "Ready", "route": route, "suggested_load": []})


def test_crew_chain_uses_recorded_outputs_and_explicit_area(monkeypatch):
    _successful_dependencies(monkeypatch)
    result = crew_lead.run_crew_brief("We have 200 lbs and four hours.", "chicago_neighborhoods", agent=FakeCrewAgent())
    assert [step["agent"] for step in result["steps"]] == ["sentry", "scout", "router", "dispatch"]
    assert result["mission_id"] == "mission-test"
    assert "Assumed study area" not in result["steps"][1]["summary"]


def test_crew_chain_discloses_inferred_area(monkeypatch):
    _successful_dependencies(monkeypatch)
    result = crew_lead.run_crew_brief("Take 200 lbs to the rural fringe in four hours.", agent=FakeCrewAgent("rural_fringe"))
    assert result["steps"][1]["summary"].startswith("Assumed study area: rural_fringe")


def test_category_intent_preserves_explicit_exclusions():
    requested, excluded = crew_lead._category_intent("200 lbs of produce, no dairy")

    assert requested == ["produce"]
    assert excluded == ["dairy"]


def test_rural_scout_scores_complete_tract_universe_before_top_n(monkeypatch):
    tracts = [{"tract_fips": str(index)} for index in range(30)]
    monkeypatch.setattr(scout_agent, "get_all_rural_tracts", lambda: tracts)
    monkeypatch.setattr(scout_agent, "load_resource_cache", lambda *args, **kwargs: [])

    def score(received, resources, top_n):
        assert received is tracts
        return [{"tract_fips": "29", "need_score": 99}]

    monkeypatch.setattr(scout_agent, "score_gaps", score)
    result = scout_agent.run_site_advisor("rural_fringe", "test", top_n=1)

    assert result["ranked_tracts"][0]["tract_fips"] == "29"


def test_crew_stops_after_failed_sentry(monkeypatch):
    monkeypatch.setattr(crew_lead, "run_watchdog", lambda area: {"status": "partial", "errors": ["boom"]})

    class StopAgent:
        def __call__(self, prompt):
            crew_lead.sentry_check("chicago_neighborhoods")

    result = crew_lead.run_crew_brief("200 lbs in four hours", agent=StopAgent())
    assert len(result["steps"]) == 1
    assert result["steps"][0]["status"] == "failed"


def test_crew_marks_a_premature_agent_stop_as_failed(monkeypatch):
    _successful_dependencies(monkeypatch)

    class PrematureAgent:
        def __call__(self, prompt):
            crew_lead.sentry_check("chicago_neighborhoods")

    result = crew_lead.run_crew_brief("200 lbs in four hours", agent=PrematureAgent())
    assert [step["agent"] for step in result["steps"]] == ["sentry", "scout"]
    assert result["steps"][-1]["status"] == "failed"
    assert result["mission_id"] is None


def test_dispatch_limits_load_to_route_demand_and_discloses_missing_cold_chain():
    class InventoryStore:
        bucket = "inventory-bucket"

        def read(self, key):
            if key == ON_HAND_KEY:
                return [{"item_id": "apples", "qty": 100, "unit_weight_lbs": 1}]
            assert key == COLD_CHAIN_KEY
            return [{"item_id": "bananas", "risk_status": "high"}]

    result = run_mission_ops(
        {
            "status": "optimal",
            "selected_stops": [{"stop_id": "tract-1", "demand": 40}],
            "route_minutes": 120,
            "capacity_used": 40,
        },
        200,
        4,
        inventory_store=InventoryStore(),
        mission_id_factory=lambda: "mission-test",
    )

    assert result["load_recommendation"]["capacity_lbs"] == 200
    assert result["load_recommendation"]["recommended_weight_lbs"] == 100
    cold_chain = next(
        check for check in result["readiness_checks"] if check["check"] == "cold_chain"
    )
    assert cold_chain["status"] == "Unknown"
    assert "apples" in cold_chain["finding"]


def test_produce_request_returns_only_produce_and_fills_requested_weight():
    class InventoryStore:
        bucket = "inventory-bucket"

        def read(self, key):
            if key == ON_HAND_KEY:
                return [
                    {"item_id": "PRD-001", "sku": "PRD-001", "item": "Fresh Produce Box", "qty": 20, "unit_weight_lbs": 12},
                    {"item_id": "PRD-002", "sku": "PRD-002", "item": "Apple Bag", "qty": 20, "unit_weight_lbs": 3},
                    {"item_id": "PRD-003", "sku": "PRD-003", "item": "Potato Bag", "qty": 20, "unit_weight_lbs": 5},
                    {"item_id": "PRD-004", "sku": "PRD-004", "item": "Whole Milk Case", "qty": 20, "unit_weight_lbs": 35},
                ]
            assert key == COLD_CHAIN_KEY
            return [
                {"item_id": "PRD-001", "risk_status": "none"},
                {"item_id": "PRD-002", "risk_status": "none"},
                {"item_id": "PRD-003", "risk_status": "none"},
                {"item_id": "PRD-004", "risk_status": "high"},
            ]

    result = run_mission_ops(
        {
            "status": "optimal",
            "selected_stops": [{"stop_id": "tract-1", "demand": 200}],
            "route_minutes": 120,
            "capacity_used": 200,
        },
        200,
        4,
        requested_categories=["produce"],
        inventory_store=InventoryStore(),
        mission_id_factory=lambda: "mission-produce",
    )

    load = result["load_recommendation"]
    assert load["recommended_weight_lbs"] == 200
    assert {item["item_id"] for item in load["items"]} == {"PRD-001", "PRD-002", "PRD-003"}
    assert load["category_match"] is True


def test_load_allocator_reconsiders_skus_to_fill_exact_weight():
    load = build_load_recommendation(
        {"selected_stops": [{"stop_id": "tract-1"}]},
        [
            {"item": "Apple Bag", "qty": 20, "unit_weight_lbs": 3},
            {"item": "Fresh Produce Box", "qty": 20, "unit_weight_lbs": 12},
            {"item": "Potato Bag", "qty": 20, "unit_weight_lbs": 5},
        ],
        24,
        requested_categories=["produce"],
    )

    assert load["recommended_weight_lbs"] == 24
    assert load["capacity_remaining_lbs"] == 0


def test_load_allocator_excludes_prohibited_category():
    load = build_load_recommendation(
        {"selected_stops": [{"stop_id": "tract-1"}]},
        [
            {"item": "Apple Bag", "qty": 100, "unit_weight_lbs": 3},
            {"item": "Whole Milk Case", "qty": 100, "unit_weight_lbs": 35},
        ],
        30,
        requested_categories=["produce"],
        excluded_categories=["dairy"],
    )

    assert load["recommended_weight_lbs"] == 30
    assert [item["item"] for item in load["items"]] == ["Apple Bag"]
