"""Unit tests for the flagged-tracts log — no AWS credentials or network
needed, same philosophy as test_gap_scorer.py: this is plain SQLite plus
Python, fully testable in isolation. Each test points the module at a fresh
temp-file database so tests never touch data/flagged_tracts.db or leak
state between runs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.flagged_tracts as flagged_tracts  # noqa: E402


def use_temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "flagged_tracts_test.db"
    monkeypatch.setattr(flagged_tracts, "DB_PATH", db_path)
    return db_path


def test_flag_then_read_round_trips(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    written = flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000",
        recommendation_type="site",
        source_agent="advisor",
        population=3210,
        centroid_lat=41.7942,
        centroid_lon=-87.6328,
        note="test flag",
    )
    assert written["status"] == "pending"

    pending = flagged_tracts.read_flagged_tracts("pending")
    assert len(pending) == 1
    assert pending[0]["tract_fips"] == "17031840000"


def test_update_moves_tract_out_of_pending(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031680000",
        recommendation_type="site",
        source_agent="advisor",
    )

    result = flagged_tracts.update_flagged_tract(
        "17031680000", "site", "resource_found", note="grocery now 0.4mi away"
    )
    assert result["status"] == "resource_found"
    assert result["last_checked_date"] is not None

    still_pending = flagged_tracts.read_flagged_tracts("pending")
    assert still_pending == []

    resolved = flagged_tracts.read_flagged_tracts("resource_found")
    assert len(resolved) == 1
    assert resolved[0]["note"] == "grocery now 0.4mi away"


def test_update_rejects_unknown_status_without_writing(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031710000", recommendation_type="site", source_agent="advisor"
    )

    result = flagged_tracts.update_flagged_tract("17031710000", "site", "bogus_status")
    assert "error" in result

    # Still pending — the bad write never happened.
    pending = flagged_tracts.read_flagged_tracts("pending")
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


def test_update_missing_tract_returns_error_not_crash(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    result = flagged_tracts.update_flagged_tract("00000000000", "site", "resource_found")
    assert "error" in result


def test_reflagging_same_tract_resets_it_to_pending(tmp_path, monkeypatch):
    """Re-recommending a tract that was previously resolved should reopen
    it for a fresh recheck, not silently duplicate the row."""
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031840000", "site", "resource_found")

    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )

    pending = flagged_tracts.read_flagged_tracts("pending")
    resolved = flagged_tracts.read_flagged_tracts("resource_found")
    assert len(pending) == 1
    assert resolved == []


def test_verify_maps_verified_open_to_resource_found(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031840000", "site", "possible_change")

    result = flagged_tracts.verify_flagged_tract("17031840000", "site", "verified_open")

    assert result["status"] == "resource_found"


def test_verify_maps_planned_not_open_to_possible_change(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031840000", "site", "possible_change")

    result = flagged_tracts.verify_flagged_tract(
        "17031840000", "site", "planned_not_open", note="opening next spring"
    )

    assert result["status"] == "possible_change"
    assert result["note"] == "opening next spring"


def test_verify_maps_incorrect_record_and_unrelated_to_still_needed(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031840000", "site", "possible_change")

    assert flagged_tracts.verify_flagged_tract("17031840000", "site", "incorrect_record")["status"] == "still_needed"

    flagged_tracts.update_flagged_tract("17031840000", "site", "possible_change")
    assert flagged_tracts.verify_flagged_tract("17031840000", "site", "unrelated")["status"] == "still_needed"


def test_verify_rejects_unknown_verification_without_writing(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    flagged_tracts.flag_tract_for_recheck(
        tract_fips="17031840000", recommendation_type="site", source_agent="advisor"
    )
    flagged_tracts.update_flagged_tract("17031840000", "site", "possible_change")

    result = flagged_tracts.verify_flagged_tract("17031840000", "site", "bogus_choice")

    assert "error" in result
    unchanged = flagged_tracts.read_flagged_tracts("possible_change")
    assert len(unchanged) == 1


def test_read_flagged_tracts_has_no_region_argument():
    """Same boundary discipline as get_low_access_tracts / get_existing_resources
    — there should be no way to point this at a different city."""
    import inspect

    sig = inspect.signature(flagged_tracts.read_flagged_tracts)
    assert "city" not in sig.parameters
    assert set(sig.parameters) <= {"status"}
