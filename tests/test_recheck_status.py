"""Unit tests for the deterministic recheck-scoring tool — pure Python, no
network or AWS dependency, same philosophy as test_gap_scorer.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.recheck_status import (  # noqa: E402
    NEARBY_THRESHOLD_MILES,
    RURAL_NEARBY_THRESHOLD_MILES,
    check_resource_appeared,
)


def test_nearby_resource_is_detected():
    result = check_resource_appeared(
        centroid_lat=41.80,
        centroid_lon=-87.63,
        resources=[{"kind": "grocery", "name": "New Grocery", "lat": 41.801, "lon": -87.631}],
        threshold_miles=1.0,
    )
    assert result["resource_now_nearby"] is True
    assert result["nearest_kind"] == "grocery"
    assert result["nearest_distance_miles"] < 1.0


def test_far_resource_is_not_counted_as_nearby():
    result = check_resource_appeared(
        centroid_lat=41.80,
        centroid_lon=-87.63,
        resources=[{"kind": "grocery", "name": "Far Grocery", "lat": 42.20, "lon": -87.90}],
        threshold_miles=1.0,
    )
    assert result["resource_now_nearby"] is False
    assert result["nearest_kind"] == "grocery"


def test_no_resources_returns_consistent_shape():
    result = check_resource_appeared(centroid_lat=41.80, centroid_lon=-87.63, resources=[])
    assert result == {
        "resource_now_nearby": False,
        "nearest_kind": None,
        "nearest_distance_miles": None,
    }


def test_picks_nearest_of_several_resources():
    result = check_resource_appeared(
        centroid_lat=41.80,
        centroid_lon=-87.63,
        resources=[
            {"kind": "convenience", "name": "Far", "lat": 42.00, "lon": -87.80},
            {"kind": "grocery", "name": "Near", "lat": 41.802, "lon": -87.632},
        ],
    )
    assert result["nearest_kind"] == "grocery"


def test_rural_threshold_is_wider_than_urban():
    """A resource ~7 miles away is correctly 'still needed' at the urban
    scale but correctly 'resolved' at the rural scale — the Watchdog must
    pick the right constant per recommendation_type (see
    watchdog_agent.py), and the two constants must actually differ for
    that to matter."""
    assert RURAL_NEARBY_THRESHOLD_MILES > NEARBY_THRESHOLD_MILES

    far_resource = [{"kind": "grocery", "name": "Distant", "lat": 37.10, "lon": -89.18}]

    urban_result = check_resource_appeared(
        centroid_lat=37.00, centroid_lon=-89.18,
        resources=far_resource, threshold_miles=NEARBY_THRESHOLD_MILES,
    )
    rural_result = check_resource_appeared(
        centroid_lat=37.00, centroid_lon=-89.18,
        resources=far_resource, threshold_miles=RURAL_NEARBY_THRESHOLD_MILES,
    )

    assert urban_result["resource_now_nearby"] is False
    assert rural_result["resource_now_nearby"] is True
