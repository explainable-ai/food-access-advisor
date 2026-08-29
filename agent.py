"""Advisor agent — the on-demand half of the Food-Access Site Recommender.

Ask it something like:
    "Where in Chicago would a new food resource have the highest impact?"

It calls, in order: get_low_access_tracts (the stock), get_existing_resources
(what's already there), score_gaps (the deterministic ranking), and
write_evidence_brief (the one LLM-generated piece) — then answers with the
ranked list and the brief for the top result.

The Watchdog agent (scheduled, checks whether a past recommendation was ever
acted on) is a separate, later piece — see the tracker notes on the elevated
scope. This file is the MVP: get this working first.
"""

from dotenv import load_dotenv
from strands import Agent, tool

from config import PILOT_CITY
from model import build_model
from tools.access_data import get_low_access_tracts
from tools.existing_resources import get_existing_resources
from tools.evidence_brief import write_evidence_brief
from tools.flagged_tracts import flag_tract_for_recheck
from tools.gap_scorer import score_gaps
from tools.telemetry import configure_telemetry, print_metrics

load_dotenv()
configure_telemetry()

SYSTEM_PROMPT = f"""You are the Food-Access Advisor for {PILOT_CITY['name']}. \
Community organizers and city planners ask you where a new food resource \
(a farm, market, or food-rescue drop point) would do the most good.

For every siting question:
1. Call get_low_access_tracts to find candidate tracts.
2. Call get_existing_resources (it takes no arguments — it always queries \
the pilot city, that's fixed in code, not something either of us can point \
elsewhere) to see what's already nearby.
3. Call score_gaps with both results to rank the candidates. Never rank \
tracts yourself — the scorer is the source of truth, you only explain it.
4. Call write_evidence_brief on the single top-ranked tract and include its \
output verbatim in your answer.
5. Call flag_top_tract_for_recheck on that same top-ranked tract. This is \
what lets the Watchdog agent check back later on whether a resource ever \
actually appeared — do this every time, not just when asked.

Always name the USDA Food Access Research Atlas as your data source. Always \
frame your answer as decision support, not a decision — a human still \
chooses. If asked about a region outside {PILOT_CITY['name']}, say plainly \
that you're only indexed for this pilot city right now, rather than \
guessing at data you don't have.
"""


@tool
def flag_top_tract_for_recheck(top_tract: dict) -> dict:
    """Log the top-ranked tract so the Watchdog can check on it later.

    A thin, Advisor-specific wrapper around `flagged_tracts.flag_tract_for_recheck`
    — `recommendation_type` ("site") and `source_agent` ("advisor") are fixed
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
        source_agent="advisor",
        population=top_tract.get("population"),
        centroid_lat=top_tract.get("centroid_lat"),
        centroid_lon=top_tract.get("centroid_lon"),
        note=f"Flagged from an Advisor recommendation (need_score={top_tract.get('need_score')}).",
    )


def build_advisor() -> Agent:
    return Agent(
        model=build_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            get_low_access_tracts,
            get_existing_resources,
            score_gaps,
            write_evidence_brief,
            flag_top_tract_for_recheck,
        ],
    )


if __name__ == "__main__":
    advisor = build_advisor()
    print(f"Food-Access Advisor ready — pilot city: {PILOT_CITY['name']}")
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
