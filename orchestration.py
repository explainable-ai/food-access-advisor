"""Formalizes the three existing agents with Strands' GraphBuilder, without
adding a fourth agent and without changing any of their tool lists, prompts,
or guardrails.

Important honesty note, confirmed directly against the installed
strands-agents package (1.54.0), not assumed: a single shared Graph cannot
do conditional cross-agent routing. GraphBuilder.add_node only accepts
AgentBase/MultiAgentBase instances (no plain-function router node), and
every entry point fires simultaneously on every graph() call -- there is no
per-call parameter to select just one. A shared graph containing Site
Advisor, Route Advisor, and Watchdog as entry points would therefore run
all three on every invocation, which is wrong here: a request is a site
question, a route question, or (on its own EventBridge schedule, decoupled
in time) a Watchdog recheck -- never more than one at once.

So each agent gets wrapped in its own trivial single-node Graph instead.
The real, honest value that adds is a uniform GraphResult (execution_order,
completed/failed node counts) and graph-level timeout/session-manager/hook
plumbing across all three agents -- it does NOT add routing logic beyond
what route_request's plain `if` below already does. The routing itself is
deliberately ordinary Python, not a graph edge condition and not an LLM
classifier: `mode` is an explicit field the caller supplies (see the
FastAPI layer in api/main.py), matching this project's existing pattern of
keeping region/mode selection out of anything the model could get wrong.
"""

from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph

from agent import build_advisor
from route_advisor import build_route_advisor
from watchdog_agent import build_watchdog


def build_site_graph() -> Graph:
    # add_node returns the GraphNode it created, not the builder -- so
    # this can't be chained the way GraphBuilder's other setters can.
    builder = GraphBuilder()
    builder.add_node(build_advisor(), node_id="site_advisor")
    return builder.build()


def build_route_graph() -> Graph:
    builder = GraphBuilder()
    builder.add_node(build_route_advisor(), node_id="route_advisor")
    return builder.build()


def build_watchdog_graph() -> Graph:
    """Included for symmetry/uniformity. watchdog_agentcore_entry.py does
    not need to switch to this -- its existing stream_async entrypoint
    already works, and wrapping it here adds no new capability there."""
    builder = GraphBuilder()
    builder.add_node(build_watchdog(), node_id="watchdog")
    return builder.build()


def route_request(mode: str, question: str):
    """Deterministic, non-LLM dispatch. `mode` is an explicit field from the
    caller -- never inferred by a model, since there is no intent
    classifier anywhere in this system and introducing one here would put
    LLM-decided routing exactly where this project keeps things
    deterministic instead.

    Args:
        mode: "site" or "route".
        question: The user's question, passed through to the graph unchanged.

    Returns:
        The GraphResult from running the matching single-node graph.
    """
    if mode == "site":
        graph = build_site_graph()
    elif mode == "route":
        graph = build_route_graph()
    else:
        raise ValueError(f"Unknown mode {mode!r}; expected 'site' or 'route'")
    return graph(question)
