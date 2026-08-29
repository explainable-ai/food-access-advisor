"""Impact-metrics tool: turns the flagged-tracts log into the design
canvas's own stated success metric.

Deliberately NOT an LLM call, same rationale as gap_scorer.py and
recheck_status.py — "unclosed gaps trending down, per region" and "median
days to resolution" are exact, reproducible numbers computed straight from
the log, not something to ask a model to summarize. Per the design
canvas's Success metric cell: tracked per region, not pooled — a rural
improvement should never be able to mask a stalled urban one, or vice
versa.

Reuses read_flagged_tracts (the existing @tool) rather than opening the
database directly — @tool-decorated functions stay plain callables (same
pattern test_gap_scorer.py notes), so this needs no new access path into
flagged_tracts.db.
"""

from datetime import date, datetime
from statistics import median

from tools.flagged_tracts import ALLOWED_STATUSES, read_flagged_tracts

REGION_BY_RECOMMENDATION_TYPE = {"site": "urban", "route": "rural"}


def _parse_date(value):
    """flagged_date is stored as a bare ISO date
    (date.today().isoformat()); last_checked_date is stored as an ISO
    datetime (datetime.now().isoformat(timespec="seconds")). A
    days-to-resolution calculation spans both columns, so both formats
    need to parse here."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.fromisoformat(value).date()


def compute_impact_metrics() -> dict:
    """Read every flagged tract (any status) and compute the design
    canvas's own success metric, per region.

    Returns:
        A dict keyed by region ("urban", "rural"), each with:
        `total_flagged`, `unclosed` (pending + still_needed), `resolved`
        (resource_found), `median_days_to_resolution` (None if no
        resolved rows yet), and `tracts` (the raw rows, for a UI to list
        individually).
    """
    metrics = {
        region: {
            "total_flagged": 0,
            "unclosed": 0,
            "resolved": 0,
            "median_days_to_resolution": None,
            "tracts": [],
        }
        for region in ("urban", "rural")
    }
    resolution_days = {"urban": [], "rural": []}

    for status in ALLOWED_STATUSES:
        for row in read_flagged_tracts(status):
            region = REGION_BY_RECOMMENDATION_TYPE.get(row.get("recommendation_type"))
            if region is None:
                continue  # unrecognized recommendation_type -- skip rather than guess its region
            bucket = metrics[region]
            bucket["total_flagged"] += 1
            bucket["tracts"].append(row)
            if row["status"] == "resource_found":
                bucket["resolved"] += 1
                flagged = _parse_date(row.get("flagged_date"))
                checked = _parse_date(row.get("last_checked_date"))
                if flagged and checked:
                    resolution_days[region].append((checked - flagged).days)
            else:
                bucket["unclosed"] += 1

    for region in ("urban", "rural"):
        if resolution_days[region]:
            metrics[region]["median_days_to_resolution"] = median(resolution_days[region])

    return metrics
