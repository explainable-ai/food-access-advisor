"""Shared geometry helper.

Split out of `gap_scorer.py` when the Watchdog needed the same straight-line
distance calculation to check whether a resource has since appeared near a
previously-flagged tract. Kept dependency-free (no shapely/geopy) since a
haversine approximation is already the documented limitation here — see
`gap_scorer.py`'s note on transit-time being the real upgrade, not a fancier
distance library.
"""

from math import atan2, cos, radians, sin, sqrt


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance between two lat/lon points, in miles."""
    r = 3958.8  # earth radius in miles
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))
