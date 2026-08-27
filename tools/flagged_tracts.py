"""Flagged-tracts log: the Watchdog's only write target.

This is the piece that turns the Advisor from a one-shot recommender into a
system that closes the loop on its own recommendation. When the Advisor (or,
later, the Route Advisor) names a top tract, it flags that tract here for a
future recheck. The Watchdog — a separate, scheduled agent, not a mode flag
on the Advisor — later reads this table, asks whether a resource has since
appeared nearby, and updates the row. Two different agents touch this table,
which is exactly why it carries `recommendation_type` and `source_agent`
columns instead of being Advisor-only: it's shared state between siblings
with different jobs and different lifecycles, not a private scratchpad.

Guardrail, same discipline as `access_data.py` / `existing_resources.py`:
`update_flagged_tract` takes a fixed, validated set of arguments and writes
to exactly one table — this one. There is no table-name parameter, no raw
SQL passed in, nothing that would let a model steer a write anywhere else.
That's what "own log only" means in the architecture diagram: not "the
Watchdog is well-behaved," but "the tool it's given cannot reach anything
else even if asked to."
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

try:
    from strands import tool
except ImportError:  # pragma: no cover - optional runtime dependency in local tests.
    def tool(func=None, **_kwargs):
        if func is None:
            return lambda f: f
        return func

DB_PATH = Path(__file__).parent.parent / "data" / "flagged_tracts.db"

ALLOWED_STATUSES = ("pending", "resource_found", "still_needed")


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS flagged_tracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tract_fips TEXT NOT NULL,
            recommendation_type TEXT NOT NULL,
            source_agent TEXT NOT NULL,
            population INTEGER,
            centroid_lat REAL,
            centroid_lon REAL,
            flagged_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            last_checked_date TEXT,
            note TEXT,
            UNIQUE(tract_fips, recommendation_type)
        )
        """
    )
    conn.commit()
    return conn


def flag_tract_for_recheck(
    tract_fips: str,
    recommendation_type: str,
    source_agent: str,
    population: int = None,
    centroid_lat: float = None,
    centroid_lon: float = None,
    note: str = "",
) -> dict:
    """Write (or refresh) one row in the shared flagged-tracts log.

    Deliberately NOT a `@tool` — this is meant to be called from inside a
    recommending agent's own tool code right after it names a top tract
    (see `agent.py`'s `write_evidence_brief` step), not offered to a model
    as something to invoke freestanding. A recommendation and a flag should
    happen together, atomically, not as two separate decisions an LLM could
    forget to make.

    Args:
        tract_fips: The recommended tract's FIPS code.
        recommendation_type: What kind of recommendation this was —
            "site" (Advisor) or "route" (the planned Route Advisor).
        source_agent: Which agent made the recommendation, e.g. "advisor".
        population, centroid_lat, centroid_lon: Carried over from the
            scored tract so the Watchdog doesn't need to re-join back to
            the Atlas DB just to know where to look.
        note: Optional free-text context (e.g. why this tract was chosen).

    Returns:
        The written row as a dict, with status set to "pending".
    """
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO flagged_tracts
                (tract_fips, recommendation_type, source_agent, population,
                 centroid_lat, centroid_lon, flagged_date, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(tract_fips, recommendation_type) DO UPDATE SET
                source_agent=excluded.source_agent,
                population=excluded.population,
                centroid_lat=excluded.centroid_lat,
                centroid_lon=excluded.centroid_lon,
                flagged_date=excluded.flagged_date,
                status='pending',
                last_checked_date=NULL,
                note=excluded.note
            """,
            (
                tract_fips,
                recommendation_type,
                source_agent,
                population,
                centroid_lat,
                centroid_lon,
                date.today().isoformat(),
                note,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM flagged_tracts WHERE tract_fips = ? AND recommendation_type = ?",
            (tract_fips, recommendation_type),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


@tool
def read_flagged_tracts(status: str = "pending") -> list:
    """Return previously-flagged tracts, for the Watchdog's recheck pass.

    Takes no city/region argument, same as `get_low_access_tracts` and
    `get_existing_resources` — every row in this table was written by an
    agent that was itself already scoped to the pilot city, so there is
    nothing outside that scope for this table to accidentally return.

    Args:
        status: One of "pending", "resource_found", "still_needed". Defaults
            to "pending" — the Watchdog's normal job is working the backlog
            of tracts nobody has checked on yet, not re-reporting ones it
            already resolved.

    Returns:
        A list of dicts (one per flagged tract), or a handful of clearly
        labeled illustrative rows if the log doesn't exist yet (e.g. the
        Advisor hasn't been run against real data). Each row has
        `tract_fips`, `recommendation_type`, `source_agent`, `population`,
        `centroid_lat`, `centroid_lon`, `flagged_date`, `status`,
        `last_checked_date`, `note`.
    """
    if status not in ALLOWED_STATUSES:
        return [{"error": f"Unknown status '{status}'. Use one of: {ALLOWED_STATUSES}"}]

    if not DB_PATH.exists():
        return [r for r in _sample_flagged_tracts() if r["status"] == status]

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM flagged_tracts WHERE status = ? ORDER BY flagged_date ASC",
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@tool
def update_flagged_tract(tract_fips: str, recommendation_type: str, status: str, note: str = "") -> dict:
    """Update one flagged tract's status after a recheck. Writes only to
    this table — there is no argument here that names a different table,
    that's the point.

    Args:
        tract_fips: Which tract to update.
        recommendation_type: "site" or "route" — matches the row written by
            `flag_tract_for_recheck`, since the same tract could in
            principle be flagged once for each recommendation type.
        status: Must be one of "pending", "resource_found", "still_needed".
            Any other value is rejected rather than silently written.
        note: Optional free-text explanation of what the recheck found
            (e.g. "grocery store now within 0.4mi per OSM").

    Returns:
        The updated row as a dict, or `{"error": ...}` if the status was
        invalid or no matching row exists — never a partial/silent write.
    """
    if status not in ALLOWED_STATUSES:
        return {"error": f"Unknown status '{status}'. Use one of: {ALLOWED_STATUSES}"}

    conn = _connect()
    try:
        cur = conn.execute(
            """
            UPDATE flagged_tracts
            SET status = ?, last_checked_date = ?, note = CASE WHEN ? != '' THEN ? ELSE note END
            WHERE tract_fips = ? AND recommendation_type = ?
            """,
            (status, datetime.now().isoformat(timespec="seconds"), note, note, tract_fips, recommendation_type),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"error": f"No flagged row for tract_fips={tract_fips!r}, recommendation_type={recommendation_type!r}"}
        row = conn.execute(
            "SELECT * FROM flagged_tracts WHERE tract_fips = ? AND recommendation_type = ?",
            (tract_fips, recommendation_type),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def _sample_flagged_tracts() -> list:
    """Illustrative placeholder rows — NOT a real recheck backlog. Lets
    `python watchdog.py` run before the Advisor has flagged anything for
    real. Matches the same-spirit fallback in `tools/access_data.py`."""
    return [
        {
            "tract_fips": "17031840000",
            "recommendation_type": "site",
            "source_agent": "advisor",
            "population": 3210,
            "centroid_lat": 41.7942,
            "centroid_lon": -87.6328,
            "flagged_date": "2026-06-01",
            "status": "pending",
            "last_checked_date": None,
            "note": "Illustrative sample row — not a real recommendation.",
        }
    ]
