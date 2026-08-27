"""Evidence-brief tool: the only LLM-backed tool in the Advisor's toolbox.

Everything upstream (access data, existing resources, gap scoring) is
deterministic and traceable on purpose — this is the one place language
generation belongs: turning an already-ranked result into the plain-language,
cited paragraph a community organizer could actually bring to a funder or a
city council meeting. This is the "agents as tools" pattern from the Strands
docs (a sub-agent wrapped in a plain @tool function), not the direct
agent-passing shortcut — we want a system prompt scoped tightly to just this
one job.
"""

from strands import Agent, tool

from model import build_model

BRIEF_SYSTEM_PROMPT = """You write short, factual briefing paragraphs for \
community organizers and city planners about food access gaps. You are \
given one already-ranked tract with its need score, population, and \
nearest-resource distance — you do not decide the ranking, you explain it.

Rules:
- Cite the USDA Food Access Research Atlas by name and note that the \
figures reflect its most recently published vintage.
- State the nearest-resource distance exactly as given — never invent or \
round it in a way that changes the claim.
- Never claim this analysis, alone, proves where a new resource should be \
built — frame it explicitly as decision support for a human decision, not \
a decision itself.
- Keep it to one paragraph, plain language, no bullet points, no headers.
"""


@tool
def write_evidence_brief(top_tract: dict) -> str:
    """Turn one ranked, scored tract into a citable, plain-language brief.

    Args:
        top_tract: One entry from `score_gaps`'s output — a dict with at
            least `tract_fips`, `population`, `need_score`, and a
            nearest-resource distance field.

    Returns:
        A single paragraph suitable for pasting into a grant application
        or a council-meeting handout, with the Atlas cited by name.
    """
    # Same pinned model as the Advisor/Watchdog (see model.py) — this
    # sub-agent would otherwise silently fall back to Strands' own shifting
    # default, which is exactly the inconsistency pinning was meant to avoid.
    brief_agent = Agent(model=build_model(), system_prompt=BRIEF_SYSTEM_PROMPT)
    response = brief_agent(f"Write the brief for this tract: {top_tract}")
    return str(response)
