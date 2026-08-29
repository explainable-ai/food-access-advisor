"""Unit tests for the impact-metrics report — pure Python + SQLite, no
Flask, no AWS dependency, same philosophy as test_flagged_tracts.py: each
test points the module at a fresh temp-file database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.flagged_tracts as flagged_tracts  # noqa: E402
from tools.impact_metrics import compute_impact_metrics  # noqa: E402


def use_temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flagged_tracts_test.db"
    monkeypatch.setattr(flagged_tracts, "DB_PATH", db_path)
    return db_path


def test_regions_are_never_pooled(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17003960100", recommendation_type="route", source_agent="route_advisor"
    )

    metrics = compute_impact_metrics()

    assert metrics["urban"]["total_flagged"] == 1
    assert metrics["rural"]["total_flagged"] == 1
    assert metrics["urban"]["tracts"][0]["tract_fips"] == "17031840000"
    assert metrics["rural"]["tracts"][0]["tract_fips"] == "17003960100"


def test_unclosed_counts_pending_and_still_needed(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031680000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031680000", "site", "still_needed")

    metrics = compute_impact_metrics()

    assert metrics["urban"]["unclosed"] == 2  # one pending, one still_needed
    assert metrics["urban"]["resolved"] == 0


def test_resolved_tract_computes_days_to_resolution(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    # Backdate flagged_date to make a real, non-zero day count.
    conn = flagged_tracts._connect()
    conn.execute(
        "UPDATE flagged_tracts SET flagged_date = ? WHERE tract_fips = ?",
        ("2026-08-01", "17031840000"),
    )
    conn.commit()
    conn.close()

    flagged_tracts.update_flagged_tract("17031840000", "site", "resource_found")

    metrics = compute_impact_metrics()

    assert metrics["urban"]["resolved"] == 1
    assert metrics["urban"]["unclosed"] == 0
    assert metrics["urban"]["median_days_to_resolution"] is not None
    assert metrics["urban"]["median_days_to_resolution"] > 0


def test_no_resolved_tracts_returns_none_median_not_a_crash(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )

    metrics = compute_impact_metrics()

    assert metrics["urban"]["median_days_to_resolution"] is None
    assert metrics["rural"]["total_flagged"] == 0
    assert metrics["rural"]["median_days_to_resolution"] is None
