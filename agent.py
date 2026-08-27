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

from strands import Agent

from config import PILOT_CITY
from tools.access_data import get_low_access_tracts
from tools.existing_resources import get_existing_resources
from tools.evidence_brief import write_evidence_brief
from tools.gap_scorer import score_gaps

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

Always name the USDA Food Access Research Atlas as your data source. Always \
frame your answer as decision support, not a decision — a human still \
chooses. If asked about a region outside {PILOT_CITY['name']}, say plainly \
that you're only indexed for this pilot city right now, rather than \
guessing at data you don't have.
"""


def build_advisor() -> Agent:
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=[
            get_low_access_tracts,
            get_existing_resources,
            score_gaps,
            write_evidence_brief,
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
        advisor(question)
