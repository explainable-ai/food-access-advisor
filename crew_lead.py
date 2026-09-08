"""Last Mile Crew orchestration layered over the existing backend agents."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Literal

from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.tools.executors import SequentialToolExecutor

from agent import run_site_advisor
from config import OPERATIONS_HUB
from model import build_model
from route_advisor import run_route_advisor
from services.mission_preview import run_mission_ops
from watchdog_agent import run_watchdog


StudyArea = Literal["chicago_neighborhoods", "rural_fringe"]
StepStatus = Literal["ok", "failed", "no_result"]


class CrewStep(BaseModel):
    agent: Literal["sentry", "scout", "router", "dispatch"]
    status: StepStatus
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)


class CrewBriefResponse(BaseModel):
    steps: list[CrewStep]
    mission_id: str | None = None
    final_summary: str


class _RunState:
    def __init__(self, explicit_area: StudyArea | None):
        self.explicit_area = explicit_area
        self.study_area: StudyArea | None = explicit_area
        self.steps: list[dict[str, Any]] = []
        self.load_lbs: float | None = None
        self.time_window_hours: float | None = None


_current_run: ContextVar[_RunState | None] = ContextVar("lastmile_crew_run", default=None)
_ORDER = ("sentry", "scout", "router", "dispatch")


def _state_for(agent: str) -> _RunState:
    state = _current_run.get()
    if state is None:
        raise RuntimeError("Crew tool called outside a Crew Lead run")
    expected = _ORDER[len(state.steps)] if len(state.steps) < len(_ORDER) else None
    if expected != agent:
        raise RuntimeError(f"Crew order violation: expected {expected}, received {agent}")
    if state.steps and state.steps[-1]["status"] != "ok":
        raise RuntimeError("Crew chain already stopped after a non-success result")
    return state


def _area(state: _RunState, requested: str) -> StudyArea:
    if requested not in {"chicago_neighborhoods", "rural_fringe"}:
        raise ValueError("Unknown study area")
    if state.explicit_area and requested != state.explicit_area:
        raise ValueError("Crew Lead changed the explicitly selected study area")
    area = state.explicit_area or requested
    if state.study_area and area != state.study_area:
        raise ValueError("Crew Lead changed the study area during the chain")
    state.study_area = area
    return area


def _append(state: _RunState, step: dict[str, Any]) -> dict[str, Any]:
    state.steps.append(step)
    return step


@tool
def sentry_check(study_area: str) -> dict:
    """Check flagged changes in one of the two supported study areas."""
    state = _state_for("sentry")
    try:
        area = _area(state, study_area)
        data = run_watchdog(area)
        if data.get("status") == "partial":
            step = {"agent": "sentry", "status": "failed", "summary": "Sentry could not complete the target-area change check.", "data": data}
        else:
            possible = int(data.get("possible_change") or 0)
            checked = int(data.get("checked") or 0)
            summary = f"Sentry checked {checked} flagged recommendation(s); {possible} possible change(s) await human verification." if checked else "No pending flagged recommendations in the target area."
            step = {"agent": "sentry", "status": "ok", "summary": summary, "data": data}
    except Exception as exc:
        step = {"agent": "sentry", "status": "failed", "summary": f"Sentry check failed: {exc}", "data": {}}
    return _append(state, step)


@tool
def scout(study_area: str, scenario: str, area_was_inferred: bool = False) -> dict:
    """Rank tracts using Scout's existing scoring path and disclose inferred areas."""
    del area_was_inferred
    state = _state_for("scout")
    try:
        area = _area(state, study_area)
        inferred = state.explicit_area is None
        data = run_site_advisor(area, scenario)
        count = int(data.get("ranked_count") or 0)
        if not data.get("top_tracts"):
            step = {"agent": "scout", "status": "no_result", "summary": "Scout found no viable tracts in the selected study area.", "data": data}
        else:
            note = f"Assumed study area: {area} (not specified in request). " if inferred else ""
            step = {"agent": "scout", "status": "ok", "summary": f"{note}Ranked {count} tracts; top 3 selected.", "data": {**data, "area_was_inferred": inferred}}
    except Exception as exc:
        step = {"agent": "scout", "status": "failed", "summary": f"Scout ranking failed: {exc}", "data": {}}
    return _append(state, step)


@tool
def router(top_tracts: list, hub: dict, time_window_hours: float, load_lbs: float) -> dict:
    """Build a constrained route from Scout's recorded top tracts."""
    del top_tracts
    state = _state_for("router")
    try:
        if time_window_hours <= 0 or load_lbs <= 0:
            raise ValueError("The request must include a positive time window and load")
        state.time_window_hours = float(time_window_hours)
        state.load_lbs = float(load_lbs)
        data = run_route_advisor(state.steps[-1]["data"]["top_tracts"], hub or OPERATIONS_HUB, time_window_hours, load_lbs)
        if data.get("status") != "optimal" or not data.get("selected_stops"):
            step = {"agent": "router", "status": "no_result", "summary": f"Router found no viable route: {data.get('reason') or 'constraints were not satisfied'}.", "data": data}
        else:
            step = {"agent": "router", "status": "ok", "summary": f"Route built: {len(data['selected_stops'])} stops, {float(data.get('route_minutes') or 0) / 60:.1f} hrs, within capacity.", "data": data}
    except Exception as exc:
        step = {"agent": "router", "status": "failed", "summary": f"Router failed: {exc}", "data": {}}
    return _append(state, step)


@tool
def dispatch(route: dict, load_lbs: float, time_window_hours: float) -> dict:
    """Draft a mission from Router's recorded route and live S3 inventory."""
    del route, load_lbs, time_window_hours
    state = _state_for("dispatch")
    try:
        data = run_mission_ops(state.steps[-1]["data"], state.load_lbs, state.time_window_hours)
        status = "no_result" if data.get("status") == "Blocked" else "ok"
        summary = "Mission is blocked by a readiness check." if status == "no_result" else "Mission drafted with suggested load — ready for human review."
        step = {"agent": "dispatch", "status": status, "summary": summary, "data": data}
    except Exception as exc:
        step = {"agent": "dispatch", "status": "failed", "summary": f"Dispatch failed: {exc}", "data": {}}
    return _append(state, step)


SYSTEM_PROMPT = """You are the Crew Lead for LastMile Market. Extract the positive load in pounds
and time window in hours from the request. Determine the study area from the explicit note when
present. Otherwise infer only chicago_neighborhoods or rural_fringe from the request; default to
chicago_neighborhoods when uncertain. Then call exactly once and strictly in order: sentry_check,
scout, router, dispatch. Pass Scout's top_tracts to Router and Router's route to Dispatch. Use the
Greater Chicago Food Depository hub when no different hub is explicitly supplied. If a tool returns
failed or no_result, stop immediately. Never invent, rewrite, summarize, or improve tool data.
"""


def build_crew_lead() -> Agent:
    return Agent(name="LastMile Market Crew Lead", model=build_model(), system_prompt=SYSTEM_PROMPT, tools=[sentry_check, scout, router, dispatch], tool_executor=SequentialToolExecutor(), callback_handler=None)


def run_crew_brief(request: str, study_area: StudyArea | None = None, *, agent: Agent | None = None) -> dict[str, Any]:
    """Run the real tool chain and return only captured tool outputs."""
    if not request.strip():
        raise ValueError("request must not be blank")
    state = _RunState(study_area)
    token = _current_run.set(state)
    try:
        prompt = request.strip()
        if study_area:
            prompt += f"\n\n(Study area specified: {study_area})"
        (agent or build_crew_lead())(prompt)
    finally:
        _current_run.reset(token)
    if not state.steps:
        raise RuntimeError("Crew Lead returned without calling Sentry")
    if len(state.steps) < len(_ORDER) and state.steps[-1]["status"] == "ok":
        next_agent = _ORDER[len(state.steps)]
        state.steps.append({"agent": next_agent, "status": "failed", "summary": f"Crew Lead stopped before {next_agent.title()} completed its required step.", "data": {}})
    final = state.steps[-1]
    mission_id = final.get("data", {}).get("mission_id") if final.get("agent") == "dispatch" else None
    return CrewBriefResponse(steps=state.steps, mission_id=mission_id, final_summary=final["summary"]).model_dump()
