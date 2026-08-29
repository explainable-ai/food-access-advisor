"""Tests for the FastAPI backend -- no real Bedrock/Overpass calls.
route_request is mocked for the two Advisor endpoints (same
no-network-in-tests philosophy as the rest of this repo); the
flagged-tracts/impact-metrics endpoints use a temp-file DB, same pattern
as test_flagged_tracts.py / test_impact_metrics.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402
import tools.flagged_tracts as flagged_tracts  # noqa: E402
from tools.existing_resources import OverpassQueryError  # noqa: E402

client = TestClient(api_main.app)


def use_temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flagged_tracts_test.db"
    monkeypatch.setattr(flagged_tracts, "DB_PATH", db_path)
    return db_path


class FakeNodeResult:
    def __init__(self, text):
        self.result = text  # str(self.result) below just needs str() to work


class FakeGraphResult:
    def __init__(self, node_id, text):
        self.results = {node_id: FakeNodeResult(text)}


def test_site_advisor_returns_answer_text(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "route_request",
        lambda mode, question: FakeGraphResult("site_advisor", "Tract 17031840000 ranks highest."),
    )

    response = client.post("/api/site-advisor", json={"question": "where's the highest-need spot?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "Tract 17031840000 ranks highest."}


def test_route_advisor_returns_answer_text(monkeypatch):
    monkeypatch.setattr(
        api_main,
        "route_request",
        lambda mode, question: FakeGraphResult("route_advisor", "A stop near tract 17003960100 would help most."),
    )

    response = client.post("/api/route-advisor", json={"question": "where would a route change help?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "A stop near tract 17003960100 would help most."}


def test_site_advisor_surfaces_overpass_failure_as_502(monkeypatch):
    def _raise(mode, question):
        raise OverpassQueryError("Overpass API unavailable after 2 attempts")

    monkeypatch.setattr(api_main, "route_request", _raise)

    response = client.post("/api/site-advisor", json={"question": "where's the highest-need spot?"})

    assert response.status_code == 502
    assert "Overpass" in response.json()["detail"]


def test_flagged_tracts_rejects_unknown_status():
    response = client.get("/api/flagged-tracts", params={"status": "made_up_status"})

    assert response.status_code == 400


def test_flagged_tracts_returns_rows_for_valid_status(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )

    response = client.get("/api/flagged-tracts", params={"status": "pending"})

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["tract_fips"] == "17031840000"


def test_impact_metrics_returns_both_regions(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17003960100", recommendation_type="route", source_agent="route_advisor"
    )

    response = client.get("/api/impact-metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["urban"]["total_flagged"] == 1
    assert body["rural"]["total_flagged"] == 1
