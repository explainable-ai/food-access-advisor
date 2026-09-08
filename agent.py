"""Scout agent — the on-demand siting member of the Last Mile Crew.

Ask it something like:
    "Where in Chicago would a new food resource have the highest impact?"

It calls rank_chicago_tracts, which uses the same complete prepared Chicago
candidate universe and resource snapshot as the workspace, then calls
write_site_evidence_brief for the single top result.

Sentry (scheduled, checks whether a past recommendation was ever
acted on) is a separate, later piece — see the tracker notes on the elevated
scope. This file is the MVP: get this working first.
"""

from dotenv import load_dotenv
from strands import Agent, tool

from config import PILOT_CITY
from model import build_model
from tools.site_evidence_brief import write_site_evidence_brief
from tools.flagged_tracts import flag_tract_for_recheck
from tools.site_ranker import rank_chicago_tracts
from tools.access_data import get_all_rural_tracts
from tools.gap_scorer import score_gaps
from tools.resource_cache import load_resource_cache
from tools.telemetry import configure_telemetry, print_metrics

load_dotenv()
configure_telemetry()

SYSTEM_PROMPT = f"""You are Scout for LastMile Market in {PILOT_CITY['name']}. \
The operations crew asks you which census tracts should be prioritized for \
the mobile market.

For every siting question:
1. Call rank_chicago_tracts to retrieve the deterministic ranking over the \
same complete prepared Chicago tract universe and resource snapshot shown in \
the Prioritize Sites workspace. Never rank tracts yourself.
2. Call write_site_evidence_brief on the single top-ranked tract and include its \
output verbatim in your answer.
3. Call flag_top_tract_for_recheck on that same top-ranked tract. This is \
what lets Sentry check back later on whether a resource ever \
actually appeared — do this every time, not just when asked.

Attribute each measure to the source carried in the scored tract: USDA Food \
Access Research Atlas for access flags, Greater Chicago Food Depository \
Community Data Map (ACS 2024) for food-insecurity risk, and Chicago Transit \
Authority static GTFS for transit evidence. Never attribute the GCFD or CTA \
measures to the Atlas. Always frame your answer as decision support, not a \
decision — a human still \
chooses. If asked about a region outside {PILOT_CITY['name']}, say plainly \
that you're only indexed for this pilot city right now, rather than \
guessing at data you don't have.
"""


@tool
def flag_top_tract_for_recheck(top_tract: dict) -> dict:
    """Log the top-ranked tract so Sentry can check on it later.

    A thin, Advisor-specific wrapper around `flagged_tracts.flag_tract_for_recheck`
    — `recommendation_type` ("site") and `source_agent` ("scout") are fixed
    here, not left as arguments the model could set, so this tool can only
    ever write a "this was a site recommendation, from the Advisor" row.
    That mirrors the boundary discipline used elsewhere in this project:
    give the model exactly the one thing it should be able to do, not a
    general-purpose write with parameters that happen to default correctly.

    Args:
        top_tract: The same top-ranked, scored dict passed to
            `write_evidence_brief` — needs at least `tract_fips`,
            `population`, `centroid_lat`, `centroid_lon`.

    Returns:
        The written flagged-tracts row.
    """
    return flag_tract_for_recheck(
        tract_fips=top_tract["tract_fips"],
        recommendation_type="site",
        source_agent="scout",
        population=top_tract.get("population"),
        centroid_lat=top_tract.get("centroid_lat"),
        centroid_lon=top_tract.get("centroid_lon"),
        note=f"Flagged from a Scout recommendation (need_score={top_tract.get('need_score')}).",
    )


def build_advisor() -> Agent:
    return Agent(
        model=build_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            rank_chicago_tracts,
            write_site_evidence_brief,
            flag_top_tract_for_recheck,
        ],
    )


def run_site_advisor(study_area: str, scenario: str, top_n: int = 12) -> dict:
    """Run Scout's existing deterministic ranking path for a Crew brief."""
    if study_area == "chicago_neighborhoods":
        ranked = rank_chicago_tracts(top_n=top_n)
    elif study_area == "rural_fringe":
        ranked = score_gaps(get_all_rural_tracts(), load_resource_cache("rural", require_complete_coverage=True), top_n=top_n)
    else:
        raise ValueError("study_area must be 'chicago_neighborhoods' or 'rural_fringe'")
    return {"study_area": study_area, "scenario": scenario, "ranked_tracts": ranked, "top_tracts": ranked[:3], "ranked_count": len(ranked)}


if __name__ == "__main__":
    advisor = build_advisor()
    print(f"LastMile Market Scout ready — pilot city: {PILOT_CITY['name']}")
    print("Ask a siting question (e.g. \"where's the highest-need spot for a "
          "new food resource?\"), or Ctrl+C to quit.\n")
    while True:
        try:
            question = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question.strip():
            continue
        result = advisor(question)
        print_metrics(result)
