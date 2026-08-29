"""Unit tests for the rural low-access tracts tool. The urban
get_low_access_tracts has no existing test file to extend (a pre-existing
gap, not introduced here) — this covers only what's new: the rural
addition.
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.access_data import get_low_access_rural_tracts  # noqa: E402


def test_get_low_access_rural_tracts_has_no_region_argument():
    """Same boundary discipline as every other tool in this project — see
    tools/existing_resources.py's test of the same shape."""
    sig = inspect.signature(get_low_access_rural_tracts)
    assert list(sig.parameters) == ["limit"]


def test_sample_fallback_returns_alexander_county_shaped_rows():
    """Without a real RURAL_DB_PATH on disk (data/prep_atlas.py doesn't
    build it yet), this must return illustrative sample rows rather than
    an empty list or an error — same fallback behavior as
    get_low_access_tracts before real Atlas data is prepped."""
    rows = get_low_access_rural_tracts()

    assert len(rows) > 0
    for row in rows:
        assert row["tract_fips"].startswith("17003")  # Alexander County, IL FIPS prefix
        assert "population" in row
        assert "low_access_half_mile" in row
        assert "low_access_one_mile" in row
        assert "centroid_lat" in row
        assert "centroid_lon" in row


def test_limit_is_respected():
    rows = get_low_access_rural_tracts(limit=1)
    assert len(rows) == 1
