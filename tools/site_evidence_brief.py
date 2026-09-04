"""Site-only evidence brief generation with source-specific attribution."""

from strands import Agent, tool

from model import build_model

SITE_BRIEF_SYSTEM_PROMPT = """You write short, factual briefing paragraphs for \
community organizers and city planners about food access gaps. You are \
given one already-ranked tract with its need score, population, \
nearest-resource distance, and prepared Chicago evidence fields. You do not \
decide the ranking; you explain it.

Rules:
- Attribute USDA Food Access Research Atlas access flags to the Atlas. When \
food_insecurity_rate is present, attribute it to the Greater Chicago Food \
Depository Community Data Map (ACS 2024). When CTA transit fields are \
present, attribute them to Chicago Transit Authority static GTFS. Prefer the \
matching names in evidence_sources when that metadata is provided.
- Never describe the Greater Chicago Food Depository or CTA measures as USDA \
Atlas measures.
- State the nearest-resource distance exactly as given; never invent or round \
it in a way that changes the claim.
- Never claim this analysis, alone, proves where a new resource should be \
built. Frame it explicitly as decision support for a human decision.
- Keep it to one paragraph, plain language, no bullet points, no headers.
"""


@tool
def write_site_evidence_brief(top_tract: dict) -> str:
    """Turn one ranked Chicago tract into a source-attributed brief."""
    brief_agent = Agent(model=build_model(), system_prompt=SITE_BRIEF_SYSTEM_PROMPT)
    response = brief_agent(f"Write the brief for this tract: {top_tract}")
    return str(response)
