"""Build the prepared Chicago food-insecurity and transportation context.

Inputs are immutable local snapshots:

* Greater Chicago Food Depository's ACS 2024 tract layer export; and
* CTA's official static GTFS ZIP.

The output is a compact, reviewable JSON overlay keyed by Census tract GEOID.
It is loaded by tools.access_data without modifying the Atlas SQLite artifact.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from tools.geo import haversine_miles


CONTEXT_FORMAT = "food-access-advisor-urban-context-v1"
FOOD_INSECURITY_SOURCE = (
    "Greater Chicago Food Depository Community Data Map, ACS 2024 "
    "(residents below 200% of the federal poverty level)"
)
CTA_SOURCE = "Chicago Transit Authority static GTFS"
NEARBY_STOP_MILES = 0.5
MAX_PROXIMITY_MILES = 0.75
FULL_ROUTE_ACCESS = 4
FULL_WEEKDAY_TRIPS = 96


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rate(value):
    if value in (None, "", "null"):
        return None
    number = float(value)
    if number > 1:
        number /= 100
    if not 0 <= number <= 1:
        raise ValueError(f"food-insecurity rate must be between 0 and 1, found {number}")
    return number


def load_food_insecurity_snapshot(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("food-insecurity snapshot contains no ArcGIS features")
    records = {}
    for feature in features:
        attributes = feature.get("attributes") or feature.get("properties") or {}
        geoid = str(attributes.get("GEOID") or "").strip()
        if len(geoid) != 11 or not geoid.isdigit():
            continue
        if geoid in records:
            raise ValueError(f"food-insecurity snapshot contains duplicate GEOID {geoid}")
        community = str(attributes.get("CookCountyCommunityArea") or "").strip()
        if community.lower() in {"", "na", "n/a", "none"}:
            community = None
        records[geoid] = {
            "food_insecurity_rate": _rate(attributes.get("Rate200FPL")),
            "food_insecurity_population": attributes.get("Count200FPL"),
            "food_insecurity_universe": attributes.get("PopPovDetermined"),
            "community_area": community,
            "is_chicago": community is not None,
        }
    if not records:
        raise ValueError("food-insecurity snapshot contains no valid tract GEOIDs")
    return records


def _weekday_service_weights(archive):
    if "calendar.txt" not in archive.namelist():
        raise ValueError("CTA GTFS feed is missing calendar.txt")
    with archive.open("calendar.txt") as raw:
        rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        weights = {}
        weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday")
        for row in rows:
            active_days = sum(int(row.get(day) or 0) for day in weekdays)
            if active_days:
                weights[row["service_id"]] = active_days / 5
    if not weights:
        raise ValueError("CTA GTFS calendar contains no weekday service")
    return weights


def load_gtfs_service(path):
    with zipfile.ZipFile(path) as archive:
        required = {"stops.txt", "trips.txt", "stop_times.txt", "calendar.txt"}
        missing = sorted(required - set(archive.namelist()))
        if missing:
            raise ValueError(f"CTA GTFS feed missing required files: {', '.join(missing)}")
        service_weights = _weekday_service_weights(archive)
        trip_routes = {}
        trip_weights = {}
        with archive.open("trips.txt") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                weight = service_weights.get(row.get("service_id"))
                trip_id = row.get("trip_id")
                route_id = row.get("route_id")
                if weight and trip_id and route_id:
                    trip_routes[trip_id] = route_id
                    trip_weights[trip_id] = weight

        stop_routes = defaultdict(set)
        stop_weekday_trips = defaultdict(float)
        with archive.open("stop_times.txt") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                trip_id = row.get("trip_id")
                stop_id = row.get("stop_id")
                route_id = trip_routes.get(trip_id)
                if not stop_id or not route_id:
                    continue
                stop_routes[stop_id].add(route_id)
                stop_weekday_trips[stop_id] += trip_weights[trip_id]

        stops = []
        with archive.open("stops.txt") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                stop_id = row.get("stop_id")
                if not stop_id or stop_id not in stop_routes:
                    continue
                try:
                    lat = float(row["stop_lat"])
                    lon = float(row["stop_lon"])
                except (KeyError, TypeError, ValueError):
                    continue
                stops.append(
                    {
                        "stop_id": stop_id,
                        "lat": lat,
                        "lon": lon,
                        "routes": stop_routes[stop_id],
                        "weekday_trips": stop_weekday_trips[stop_id],
                    }
                )
    if not stops:
        raise ValueError("CTA GTFS feed contains no usable scheduled stops")
    return stops


def transit_metrics(lat, lon, stops):
    nearby_routes = set()
    best_weekday_trips = 0.0
    nearest = math.inf
    for stop in stops:
        distance = haversine_miles(lat, lon, stop["lat"], stop["lon"])
        nearest = min(nearest, distance)
        if distance <= NEARBY_STOP_MILES:
            nearby_routes.update(stop["routes"])
            best_weekday_trips = max(best_weekday_trips, stop["weekday_trips"])
    if not math.isfinite(nearest):
        return None
    proximity_access = 1 - min(nearest / MAX_PROXIMITY_MILES, 1)
    route_access = min(len(nearby_routes) / FULL_ROUTE_ACCESS, 1)
    frequency_access = min(best_weekday_trips / FULL_WEEKDAY_TRIPS, 1)
    access = 0.4 * proximity_access + 0.3 * route_access + 0.3 * frequency_access
    return {
        "transit_burden": round(1 - access, 4),
        "transit_nearest_stop_miles": round(nearest, 3),
        "transit_route_count": len(nearby_routes),
        "transit_weekday_trips": round(best_weekday_trips, 1),
    }


def _tracts(database_path):
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT tract_fips, centroid_lat, centroid_lon FROM tracts ORDER BY tract_fips"
        ).fetchall()
    if not rows:
        raise ValueError("prepared Atlas database contains no tracts")
    return rows


def build_context(database_path, food_insecurity_path, gtfs_path, *, generated_at=None):
    food = load_food_insecurity_snapshot(food_insecurity_path)
    stops = load_gtfs_service(gtfs_path)
    records = []
    missing_food = []
    chicago_count = 0
    transit_count = 0
    for geoid, lat, lon in _tracts(database_path):
        context = food.get(geoid)
        if context is None:
            missing_food.append(geoid)
            continue
        record = {"tract_fips": geoid, **context}
        if context["is_chicago"]:
            chicago_count += 1
            if lat is None or lon is None:
                record.update(
                    {
                        "transit_burden": None,
                        "transit_nearest_stop_miles": None,
                        "transit_route_count": None,
                        "transit_weekday_trips": None,
                    }
                )
            else:
                metrics = transit_metrics(float(lat), float(lon), stops)
                record.update(metrics or {})
                transit_count += int(metrics is not None)
        else:
            record.update(
                {
                    "transit_burden": None,
                    "transit_nearest_stop_miles": None,
                    "transit_route_count": None,
                    "transit_weekday_trips": None,
                }
            )
        record["scoring_context_version"] = CONTEXT_FORMAT
        records.append(record)
    if missing_food:
        raise ValueError(
            f"food-insecurity snapshot is missing {len(missing_food)} prepared tracts"
        )
    if transit_count != chicago_count:
        raise ValueError(
            f"CTA transportation metrics matched {transit_count} of {chicago_count} Chicago tracts"
        )
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "context_format": CONTEXT_FORMAT,
        "generated_at": timestamp,
        "tract_count": len(records),
        "chicago_tract_count": chicago_count,
        "transportation_scored_tract_count": transit_count,
        "sources": {
            "food_insecurity": {
                "name": FOOD_INSECURITY_SOURCE,
                "snapshot_sha256": _sha256(food_insecurity_path),
            },
            "transportation": {
                "name": CTA_SOURCE,
                "snapshot_sha256": _sha256(gtfs_path),
                "method": (
                    "40% stop proximity, 30% nearby route availability, "
                    "30% average weekday scheduled service"
                ),
                "nearby_stop_miles": NEARBY_STOP_MILES,
            },
        },
        "records": records,
    }


def write_context(path, payload):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--food-insecurity-snapshot", type=Path, required=True)
    parser.add_argument("--cta-gtfs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_context(
        args.database,
        args.food_insecurity_snapshot,
        args.cta_gtfs,
    )
    write_context(args.output, payload)
    print(
        f"Wrote {payload['tract_count']} tract context rows "
        f"({payload['transportation_scored_tract_count']} with CTA transportation evidence)"
    )


if __name__ == "__main__":
    main()
