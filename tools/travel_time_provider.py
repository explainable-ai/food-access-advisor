"""Road-network travel times and directions supplied by openrouteservice."""

import os

import requests


class TravelTimeProviderError(RuntimeError):
    pass


class OpenRouteServiceProvider:
    def __init__(self, *, api_key=None, base_url=None, session=None):
        self.api_key = api_key or os.getenv("OPENROUTESERVICE_API_KEY")
        self.base_url = (base_url or os.getenv("OPENROUTESERVICE_BASE_URL") or "https://api.openrouteservice.org").rstrip("/")
        self.session = session or requests
        if not self.api_key:
            raise TravelTimeProviderError("OPENROUTESERVICE_API_KEY is not configured")

    @property
    def headers(self):
        return {"Authorization": self.api_key, "Content-Type": "application/json", "Accept": "application/json, application/geo+json"}

    def _post(self, path, body):
        try:
            response = self.session.post(f"{self.base_url}{path}", headers=self.headers, json=body, timeout=45)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status})" if status else ""
            raise TravelTimeProviderError(f"openrouteservice request failed{suffix}") from exc
        except ValueError as exc:
            raise TravelTimeProviderError("openrouteservice returned invalid JSON") from exc

    def calculate_matrix(self, points):
        if not 2 <= len(points) <= 16:
            raise ValueError("openrouteservice matrix requires 2-16 points for this optimizer")
        payload = self._post("/v2/matrix/driving-car", {
            "locations": [[float(point["lon"]), float(point["lat"])] for point in points],
            "metrics": ["duration"],
        })
        durations = payload.get("durations")
        if not isinstance(durations, list) or len(durations) != len(points):
            raise TravelTimeProviderError("openrouteservice returned an incomplete route matrix")
        matrix = []
        for row in durations:
            if not isinstance(row, list) or len(row) != len(points) or any(value is None for value in row):
                raise TravelTimeProviderError("openrouteservice returned an incomplete route-matrix row")
            matrix.append([round(float(value) / 60, 3) for value in row])
        return matrix

    def directions(self, points, alternatives=2):
        coordinates = [[float(point["lon"]), float(point["lat"])] for point in points]
        body = {"coordinates": coordinates, "instructions": True, "units": "mi"}
        if len(coordinates) == 2 and alternatives:
            body["alternative_routes"] = {
                "target_count": min(int(alternatives), 2),
                "weight_factor": 1.6,
                "share_factor": 0.6,
            }
        payload = self._post("/v2/directions/driving-car/geojson", body)
        features = payload.get("features") if isinstance(payload, dict) else None
        if not features:
            raise TravelTimeProviderError("openrouteservice returned no road geometry")
        routes = [_parse_feature(feature) for feature in features]

        # ORS' built-in alternative_routes option applies to a simple
        # origin/destination trip. A service route normally has waypoints, so
        # produce honest alternatives by comparing a shortest-path request and
        # (when possible) the reverse stop order while preserving both ends.
        # Each option is still a real ORS road route; no straight connector is
        # synthesized.
        if len(coordinates) > 2 and alternatives:
            variants = [
                {**body, "preference": "shortest"},
            ]
            interior = coordinates[1:-1]
            if len(interior) > 1:
                variants.append({**body, "coordinates": [coordinates[0], *reversed(interior), coordinates[-1]]})
            for variant in variants[: int(alternatives)]:
                try:
                    candidate_payload = self._post("/v2/directions/driving-car/geojson", variant)
                except TravelTimeProviderError:
                    continue
                for feature in candidate_payload.get("features") or []:
                    candidate = _parse_feature(feature)
                    if candidate["coordinates"] and not _same_route(candidate, routes):
                        routes.append(candidate)
                        break
                if len(routes) >= int(alternatives) + 1:
                    break
        return routes[: int(alternatives) + 1]


def _parse_feature(feature):
    properties = feature.get("properties") or {}
    summary = properties.get("summary") or {}
    legs = []
    for index, segment in enumerate(properties.get("segments") or []):
        steps = [{
            "instruction": str(step.get("instruction") or ""),
            "distanceMiles": step.get("distance"),
            "durationMinutes": round(float(step["duration"]) / 60, 3) if step.get("duration") is not None else None,
        } for step in segment.get("steps") or []]
        legs.append({
            "index": index,
            "distanceMiles": segment.get("distance"),
            "durationMinutes": round(float(segment["duration"]) / 60, 3) if segment.get("duration") is not None else None,
            "steps": steps,
        })
    return {
        "coordinates": feature.get("geometry", {}).get("coordinates") or [],
        "legs": legs,
        "distanceMiles": summary.get("distance"),
        "durationMinutes": round(float(summary["duration"]) / 60, 3) if summary.get("duration") is not None else None,
    }


def _same_route(candidate, routes):
    coordinates = candidate.get("coordinates") or []
    signature = tuple((round(float(lon), 5), round(float(lat), 5)) for lon, lat in coordinates)
    for route in routes:
        other = route.get("coordinates") or []
        other_signature = tuple((round(float(lon), 5), round(float(lat), 5)) for lon, lat in other)
        if signature == other_signature:
            return True
    return False


def get_openrouteservice_matrix(points):
    return OpenRouteServiceProvider().calculate_matrix(points)


def get_openrouteservice_directions(points, alternatives=2):
    routes = OpenRouteServiceProvider().directions(points, alternatives=alternatives)
    primary = dict(routes[0])
    primary["alternatives"] = routes[1:]
    return primary
