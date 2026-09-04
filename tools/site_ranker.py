"""Shared prepared-data ranking path for the Chicago Site Advisor."""

from strands import tool

from tools.access_data import (
    PreparedTractDataError,
    get_all_tracts,
    get_low_access_tracts,
)
from tools.existing_resources import get_existing_resources
from tools.gap_scorer import score_gaps
from tools.resource_cache import load_resource_cache


@tool
def rank_chicago_tracts(top_n: int = 25) -> list:
    """Rank Chicago tracts, retaining the documented local sample smoke test.

    Prepared deployments always use the complete Chicago universe and the
    verified resource snapshot. A fresh checkout, where the tract reader's
    explicit fallback returns only clearly labelled sample rows, keeps the
    original live-resource smoke-test path. Malformed or incomplete prepared
    data never falls back to sample scoring.
    """
    sample_mode = False
    try:
        tracts = [tract for tract in get_all_tracts() if tract.get("is_chicago")]
    except PreparedTractDataError as prepared_error:
        fallback_tracts = get_low_access_tracts(limit=top_n)
        if not fallback_tracts or not all(
            tract.get("data_mode") == "sample" for tract in fallback_tracts
        ):
            raise prepared_error
        tracts = fallback_tracts
        sample_mode = True

    resources = (
        get_existing_resources()
        if sample_mode
        else load_resource_cache("urban", require_complete_coverage=True)
    )
    return score_gaps(tracts, resources, top_n=top_n)
