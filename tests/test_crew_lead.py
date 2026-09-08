import crew_lead


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
    monkeypatch.setattr(crew_lead, "run_mission_ops", lambda route, load, hours: {"mission_id": "mission-test", "status": "Ready", "route": route, "suggested_load": []})


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
