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
        return [_parse_feature(feature) for feature in features]


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


def get_openrouteservice_matrix(points):
    return OpenRouteServiceProvider().calculate_matrix(points)


def get_openrouteservice_directions(points, alternatives=2):
    routes = OpenRouteServiceProvider().directions(points, alternatives=alternatives)
    primary = dict(routes[0])
    primary["alternatives"] = routes[1:]
    return primary
