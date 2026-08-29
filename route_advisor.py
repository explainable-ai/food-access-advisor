"""Route Advisor agent — the rural sibling of the Site Advisor.

Same shape as `agent.py`'s Advisor (one Agent, tools called in a fixed
order, zero region arguments — pinned to config.PILOT_RURAL_COUNTY exactly
as `agent.py` is pinned to config.PILOT_CITY), but it recommends a route or
distribution-schedule change against existing distribution networks
instead of a new fixed site — a fixed-site store is often not viable at
rural density (see the rural systems-thinking pass in
docs/design-canvas.html for the full reasoning).

Deliberately a separate agent, not `agent.py` with a `region="rural"` flag:
same reasoning as the Advisor/Watchdog split — a single agent with a flag
that changes its behavior is one prompt-injection or bug away from running
in the wrong mode, and a flag would reopen exactly the model-controlled
surface `tools/access_data.py` and `tools/existing_resources.py` were
fixed to close.

Status: scaffolded against illustrative sample data only (see
`tools/access_data.py`'s `_sample_rural_tracts`) — `data/prep_atlas.py`
does not yet build a real Alexander County database. Same maturity level
the Site Advisor started at before real Atlas data was prepped for
Chicago.

Run: python route_advisor.py
"""

from dotenv import load_dotenv
from strands import Agent, tool

from config import PILOT_RURAL_COUNTY
from model import build_model
from tools.access_data import get_low_access_rural_tracts
from tools.evidence_brief import write_route_brief
from tools.existing_resources import get_rural_existing_resources
from tools.flagged_tracts import flag_tract_for_recheck
from tools.gap_scorer import score_gaps
from tools.telemetry import configure_telemetry, print_metrics

load_dotenv()
configure_telemetry()

SYSTEM_PROMPT = f"""You are the Route Advisor for {PILOT_RURAL_COUNTY['name']}. \
Community organizers and regional planners ask you where a route or \
distribution-schedule change — a mobile market stop, a food-bank delivery \
day — would do the most good. You do not recommend new brick-and-mortar \
sites; that's the Site Advisor's job, and a fixed-site store is often not \
viable at rural density.

For every routing question:
1. Call get_low_access_rural_tracts to find candidate tracts.
2. Call get_rural_existing_resources (it takes no arguments — it always \
queries {PILOT_RURAL_COUNTY['name']}, fixed in code, not something either \
of us can point elsewhere) to see what's already nearby.
3. Call score_gaps with both results to rank the candidates. Never rank \
tracts yourself — the scorer is the source of truth, you only explain it.
4. Call write_route_brief on the single top-ranked tract and include its \
output verbatim in your answer. It will name the route-capacity \
trade-off — you must not omit or soften that when you relay it, since a \
mobile route has fixed stop capacity and "add a stop here" is usually \
really "move a stop from somewhere else."
5. Call flag_top_route_for_recheck on that same top-ranked tract. This is \
what lets the Watchdog check back later on whether a route or schedule \
change ever actually happened — do this every time, not just when asked.

Always name the USDA Food Access Research Atlas as your data source. \
Always frame your answer as decision support, not a decision — a human \
still chooses. If asked about a region outside {PILOT_RURAL_COUNTY['name']}, \
say plainly that you're only indexed for this rural pilot county right \
now, rather than guessing at data you don't have.
"""


@tool
def flag_top_route_for_recheck(top_tract: dict) -> dict:
    """Log the top-ranked tract so the Watchdog can check on it later.

    A thin, Route-Advisor-specific wrapper around
    `flagged_tracts.flag_tract_for_recheck` — `recommendation_type`
    ("route") and `source_agent` ("route_advisor") are fixed here, not
    left as arguments the model could set, same discipline as `agent.py`'s
    `flag_top_tract_for_recheck`.

    Args:
        top_tract: The same top-ranked, scored dict passed to
            `write_route_brief` — needs at least `tract_fips`,
            `population`, `centroid_lat`, `centroid_lon`.

    Returns:
        The written flagged-tracts row.
    """
    return flag_tract_for_recheck(
        tract_fips=top_tract["tract_fips"],
        recommendation_type="route",
        source_agent="route_advisor",
        population=top_tract.get("population"),
        centroid_lat=top_tract.get("centroid_lat"),
        centroid_lon=top_tract.get("centroid_lon"),
        note=f"Flagged from a Route Advisor recommendation (need_score={top_tract.get('need_score')}).",
    )


def build_route_advisor() -> Agent:
    return Agent(
        model=build_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            get_low_access_rural_tracts,
            get_rural_existing_resources,
            score_gaps,
            write_route_brief,
            flag_top_route_for_recheck,
        ],
    )


if __name__ == "__main__":
    route_advisor = build_route_advisor()
    print(f"Route Advisor ready — rural pilot county: {PILOT_RURAL_COUNTY['name']}")
    print("Ask a routing question (e.g. \"where would a route change help most?\"), "
          "or Ctrl+C to quit.\n")
    while True:
        try:
            question = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question.strip():
            continue
        result = route_advisor(question)
        print_metrics(result)
