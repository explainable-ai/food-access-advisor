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

The rural Atlas database can be prepared with `data/prep_atlas.py`; until
that happens the access tool returns rows explicitly labelled as samples.
Constrained route scenarios are computed deterministically by
`tools/route_optimizer.py` and exposed through the FastAPI backend, not
decided by the language model.

Run: python route_advisor.py
"""

from dotenv import load_dotenv
from strands import Agent, tool

from config import OPERATIONS_HUB, PILOT_RURAL_COUNTY
from model import build_model
from services.load_recommendation import assess_inventory_feasibility
from storage.inventory import ON_HAND_KEY, S3InventoryStore
from tools.access_data import get_low_access_rural_tracts
from tools.evidence_brief import write_route_brief
from tools.existing_resources import get_rural_existing_resources
from tools.flagged_tracts import flag_tract_for_recheck
from tools.gap_scorer import score_gaps
from tools.telemetry import configure_telemetry, print_metrics
from tools.route_optimizer import optimize_route
from tools.travel_time_provider import get_road_route_matrix

load_dotenv()
configure_telemetry()


CONTRIBUTION_LABELS = {
    "food_access_gap": "food-access gap",
    "poverty": "economic hardship",
    "no_vehicle": "households without a vehicle",
    "population_served": "population reach",
    "transit_burden": "transit burden",
    "existing_coverage": "limited existing coverage",
}


def _stop_name(tract: dict, index: int) -> str:
    for key in ("community_area", "place_name", "municipality", "name", "tract_name"):
        value = str(tract.get(key) or "").strip()
        if value:
            return value
    region = str(tract.get("region_name") or tract.get("county_name") or "").strip()
    return f"{region or 'Selected study area'} priority area {tract.get('rank') or index + 1}"


def _selection_reason(tract: dict) -> str:
    contributions = tract.get("score_contributions") or {}
    drivers = [
        CONTRIBUTION_LABELS.get(key, key.replace("_", " "))
        for key, value in sorted(
            contributions.items(), key=lambda item: float(item[1] or 0), reverse=True
        )
        if float(value or 0) > 0
    ][:2]
    households = tract.get("households_total")
    parts = [
        f"Scout rank {tract.get('rank') or '—'} with need score {float(tract.get('need_score') or 0):.0f}"
    ]
    if drivers:
        parts.append(f"strongest measured factors: {', '.join(drivers)}")
    if households is not None:
        parts.append(f"represents {float(households):,.0f} households")
    return "; ".join(parts) + "."


SYSTEM_PROMPT = f"""You are Router, LastMile Market's route-planning agent for {PILOT_RURAL_COUNTY['name']}. \
Community organizers and regional planners ask you where a route or \
distribution-schedule change — a mobile market stop, a food-bank delivery \
day — would do the most good. You do not recommend new brick-and-mortar \
sites; Scout ranks priority areas, and a fixed-site store is often not \
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
what lets Sentry check back later on whether a route or schedule \
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
        note=f"Flagged from a Router recommendation (need_score={top_tract.get('need_score')}).",
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


def _inventory_route_capacity(load_lbs: float, inventory_store=None) -> tuple[float, dict]:
    """Cap route demand at usable inventory when S3 inventory is available.

    This is intentionally a gross-weight preflight. Dispatch still performs
    the stricter category, cold-chain, spoilage, and per-stop allocation checks.
    If inventory cannot be read, Router preserves existing behavior and marks
    the preflight unknown instead of silently pretending inventory was checked.
    """
    try:
        store = inventory_store or S3InventoryStore()
        inventory = store.read(ON_HAND_KEY)
        feasibility = assess_inventory_feasibility(inventory, load_lbs)
    except Exception as exc:
        return float(load_lbs), {
            "status": "unknown",
            "requested_load_lbs": round(float(load_lbs), 2),
            "effective_load_lbs": round(float(load_lbs), 2),
            "reason": f"Inventory preflight unavailable: {exc}",
        }
    effective = float(feasibility.get("effective_load_lbs") or 0)
    return effective, feasibility


def run_route_advisor(
    top_tracts: list,
    hub: dict | None,
    time_window_hours: float,
    load_lbs: float,
    *,
    matrix_fn=None,
    inventory_store=None,
) -> dict:
    """Build Router's constrained route from Scout's actual top tracts.

    Route capacity is capped by current usable S3 inventory before optimization
    when inventory is available. Dispatch later performs the detailed item-level
    and equity-aware load plan, so Router never invents product quantities.
    """
    if not top_tracts:
        return {
            "status": "infeasible",
            "reason": "Scout returned no candidate tracts",
            "travel_time_source": "not_run",
            "selected_stops": [],
            "unselected_stops": [],
        }
    effective_load_lbs, inventory_feasibility = _inventory_route_capacity(
        load_lbs, inventory_store=inventory_store
    )
    if effective_load_lbs <= 0:
        return {
            "status": "infeasible",
            "reason": "Current inventory cannot support a positive route load",
            "travel_time_source": "not_run",
            "selected_stops": [],
            "unselected_stops": [],
            "inventory_feasibility": inventory_feasibility,
        }
    origin = hub or OPERATIONS_HUB
    candidate_tracts = top_tracts[:5]
    represented_households = [
        max(float(row.get("households_total") or 0), 0) for row in candidate_tracts
    ]
    demand_weights = [
        households if households > 0 else max(float(row.get("population") or 1), 1)
        for row, households in zip(candidate_tracts, represented_households)
    ]
    total_households = sum(demand_weights)
    candidates = []
    allocated = 0.0
    for index, (tract, household_count, demand_weight) in enumerate(
        zip(candidate_tracts, represented_households, demand_weights)
    ):
        demand = (
            effective_load_lbs - allocated
            if index == len(candidate_tracts) - 1
            else round(effective_load_lbs * demand_weight / total_households, 2)
        )
        allocated += demand
        candidates.append(
            {
                "stop_id": str(tract.get("tract_fips")),
                "tract_fips": str(tract.get("tract_fips")),
                "lat": tract.get("centroid_lat"),
                "lon": tract.get("centroid_lon"),
                "demand": max(demand, 0.01),
                "households": household_count,
                "need_score": float(tract.get("need_score") or 0),
                "neighborhood_vulnerability_score": float(tract.get("need_score") or 0),
                "rank": tract.get("rank") or index + 1,
                "name": _stop_name(tract, index),
                "selection_reason": _selection_reason(tract),
                "community_area": tract.get("community_area"),
                "population": tract.get("population"),
                "score_components": tract.get("score_components") or {},
                "score_contributions": tract.get("score_contributions") or {},
                "score_explanation": tract.get("score_explanation"),
                "currently_served": False,
            }
        )
    provider = matrix_fn or get_road_route_matrix
    matrix = provider([origin, *candidates])
    route = optimize_route(
        candidates=candidates,
        depot={"lat": origin["lat"], "lon": origin["lon"]},
        max_route_minutes=time_window_hours * 60,
        vehicle_capacity=effective_load_lbs,
        max_stops=min(4, len(candidates)),
        service_minutes=20,
        travel_time_matrix=matrix,
        travel_time_source="road_network_matrix",
    )
    return {
        **route,
        "hub": origin,
        "requested_load_lbs": round(float(load_lbs), 2),
        "effective_route_load_lbs": round(effective_load_lbs, 2),
        "inventory_feasibility": inventory_feasibility,
    }


if __name__ == "__main__":
    route_advisor = build_route_advisor()
    print(f"LastMile Market Router ready — rural pilot county: {PILOT_RURAL_COUNTY['name']}")
    print(
        "Ask a routing question (e.g. \"where would a route change help most?\"), "
        "or Ctrl+C to quit.\n"
    )
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
