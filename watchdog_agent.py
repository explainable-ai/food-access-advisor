"""Watchdog agent — the scheduled half of the Food-Access Site Recommender.

Where the Advisor answers an on-demand question, the Watchdog runs on a
schedule (e.g. monthly, via EventBridge in the AWS architecture — see
diagrams/system_architecture.py) with no human in the loop asking it
anything. Its one job: work through the backlog of previously-flagged
tracts and report whether a food resource ever actually appeared nearby.

This is deliberately a separate agent, not a `mode="watchdog"` flag on the
Advisor. Same reasoning as the region-boundary fix in `tools/access_data.py`
and `tools/existing_resources.py`: a single agent with a flag that changes
its behavior is one prompt-injection or bug away from running in the wrong
mode. Two agents with disjoint tool lists can't do that — the Watchdog
literally has no tool that can answer a siting question, and the Advisor
has no tool that can write to the flagged-tracts log's status field.

Run: python watchdog_agent.py

Naming note: this file is `watchdog_agent.py`, not `watchdog.py` — Strands
itself depends on the third-party `watchdog` filesystem-watcher package
(used for tool hot-reloading), and a same-named file at the project root
shadows it on `sys.path`, breaking `strands`' own import of `Agent` with a
circular-import error. Found by actually running the test suite, not
assumed.
"""

from strands import Agent

from config import PILOT_CITY
from model import build_model
from tools.existing_resources import get_existing_resources
from tools.flagged_tracts import read_flagged_tracts, update_flagged_tract
from tools.recheck_status import check_resource_appeared

SYSTEM_PROMPT = f"""You are the Food-Access Watchdog for {PILOT_CITY['name']}. \
You run on a schedule, unattended — nobody is asking you a question right \
now. Your job is to work the backlog of tracts previously flagged by a \
recommending agent (like the Advisor) and report, per tract, whether a food \
resource has since actually appeared nearby.

For this run:
1. Call read_flagged_tracts (default status="pending") to get the backlog.
2. If it's empty, say so plainly and stop — do not invent tracts to check.
3. Call get_existing_resources once (it takes no arguments — it always \
queries the pilot city, same boundary as everywhere else in this project) \
to get a fresh, live read of what's nearby right now.
4. For each flagged tract, call check_resource_appeared with that tract's \
centroid and the resources you just fetched. Never eyeball distances \
yourself — that tool's threshold is the source of truth, you only relay it.
5. Call update_flagged_tract for that tract: status="resource_found" if \
check_resource_appeared says resource_now_nearby is true, otherwise \
status="still_needed". Always pass a short note explaining what you found \
(the nearest kind and distance, or that nothing turned up).
6. Finish with a short summary: how many tracts you checked, how many \
resolved, how many are still needed.

You never make a new siting recommendation — that's the Advisor's job, not \
yours. You only report on what already happened to a past one.
"""


def build_watchdog() -> Agent:
    return Agent(
        model=build_model(),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            read_flagged_tracts,
            get_existing_resources,
            check_resource_appeared,
            update_flagged_tract,
        ],
    )


if __name__ == "__main__":
    watchdog = build_watchdog()
    print(f"Food-Access Watchdog — pilot city: {PILOT_CITY['name']}")
    print("Running a single unattended recheck pass over the flagged-tracts backlog...\n")
    watchdog("Run today's recheck pass over every pending flagged tract.")
