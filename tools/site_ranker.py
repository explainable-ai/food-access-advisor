"""Shared prepared-data ranking path for the Chicago Site Advisor."""

from strands import tool

from tools.access_data import get_all_tracts
from tools.gap_scorer import score_gaps
from tools.resource_cache import load_resource_cache


@tool
def rank_chicago_tracts(top_n: int = 25) -> list:
    """Rank the same all-Chicago candidate universe shown in the workspace."""
    tracts = [tract for tract in get_all_tracts() if tract.get("is_chicago")]
    resources = load_resource_cache("urban", require_complete_coverage=True)
    return score_gaps(tracts, resources, top_n=top_n)
