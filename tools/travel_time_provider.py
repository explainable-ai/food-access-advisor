"""Road-network travel times and directions from configured providers."""

import os

import boto3
import requests


METERS_PER_MILE = 1609.344


class TravelTimeProviderError(RuntimeError):
    pass


class AmazonLocationRoutesProvider:
    """Use the ECS task role to call Amazon Location Routes V2."""

    def __init__(self, *, region=None, client=None):
        self.region = region or os.getenv("AWS_LOCATION_REGION") or os.getenv("AWS_REGION") or "us-east-1"
        self.client = client or boto3.client("geo-routes", region_name=self.region)

    def _call(self, operation, **kwargs):
        try:
            return getattr(self.client, operation)(**kwargs)
        except Exception as exc:
            raise TravelTimeProviderError(f"Amazon Location {operation} request failed") from exc

    def calculate_matrix(self, points):
        if not 2 <= len(points) <= 16:
            raise ValueError("Amazon Location matrix requires 2-16 points for this optimizer")
        positions = [{"Position": _position(point)} for point in points]
        payload = self._call(
            "calculate_route_matrix",
            Origins=positions,
            Destinations=positions,
            RoutingBoundary={"Unbounded": True},
            OptimizeRoutingFor="FastestRoute",
            Traffic={"Usage": "UseTrafficData"},
            TravelMode="Car",
        )
        rows = payload.get("RouteMatrix")
        if not isinstance(rows, list) or len(rows) != len(points):
            raise TravelTimeProviderError("Amazon Location returned an incomplete route matrix")
        matrix = []
        for row in rows:
            if not isinstance(row, list) or len(row) != len(points):
                raise TravelTimeProviderError("Amazon Location returned an incomplete route-matrix row")
            parsed_row = []
            for cell in row:
                if not isinstance(cell, dict) or cell.get("Error") or cell.get("Duration") is None:
                    raise TravelTimeProviderError("Amazon Location could not route every stop pair")
                parsed_row.append(round(float(cell["Duration"]) / 60, 3))
            matrix.append(parsed_row)
        return matrix

    def directions(self, points, alternatives=2):
        if not 2 <= len(points) <= 16:
            raise ValueError("Amazon Location directions require 2-16 points")
        payload = self._call(
            "calculate_routes",
            Origin=_position(points[0]),
            Destination=_position(points[-1]),
            Waypoints=[{"Position": _position(point)} for point in points[1:-1]],
            InstructionsMeasurementSystem="Imperial",
            LegAdditionalFeatures=["Summary", "TravelStepInstructions"],
            LegGeometryFormat="Simple",
            MaxAlternatives=int(alternatives),
            OptimizeRoutingFor="FastestRoute",
            Traffic={"Usage": "UseTrafficData"},
            TravelMode="Car",
        )
        routes = payload.get("Routes") if isinstance(payload, dict) else None
        if not routes:
            raise TravelTimeProviderError("Amazon Location returned no road geometry")
        return [_parse_amazon_route(route) for route in routes[: int(alternatives) + 1]]


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
            "locations": [_position(point) for point in points],
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
        coordinates = [_position(point) for point in points]
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
        if len(coordinates) > 2 and alternatives:
            for variant in [{**body, "preference": "shortest"}][: int(alternatives)]:
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


def _position(point):
    return [float(point["lon"]), float(point["lat"])]


def _miles(value):
    return round(float(value) / METERS_PER_MILE, 3) if value is not None else None


def _minutes(value):
    return round(float(value) / 60, 3) if value is not None else None


def _parse_amazon_route(route):
    legs = []
    coordinates = []
    for index, leg in enumerate(route.get("Legs") or []):
        line = (leg.get("Geometry") or {}).get("LineString") or []
        if coordinates and line and coordinates[-1] == line[0]:
            coordinates.extend(line[1:])
        else:
            coordinates.extend(line)
        steps = [{
            "instruction": str(step.get("Instruction") or ""),
            "distanceMiles": _miles(step.get("Distance")),
            "durationMinutes": _minutes(step.get("Duration")),
        } for step in leg.get("TravelSteps") or []]
        legs.append({
            "index": index,
            "distanceMiles": _miles(leg.get("Distance")),
            "durationMinutes": _minutes(leg.get("Duration")),
            "steps": steps,
        })
    if not coordinates:
        raise TravelTimeProviderError("Amazon Location returned no road geometry")
    return {
        "coordinates": coordinates,
        "legs": legs,
        "distanceMiles": _miles(route.get("Distance")),
        "durationMinutes": _minutes(route.get("Duration")),
    }


def _parse_feature(feature):
    properties = feature.get("properties") or {}
    summary = properties.get("summary") or {}
    legs = []
    for index, segment in enumerate(properties.get("segments") or []):
        steps = [{
            "instruction": str(step.get("instruction") or ""),
            "distanceMiles": step.get("distance"),
            "durationMinutes": _minutes(step.get("duration")),
        } for step in segment.get("steps") or []]
        legs.append({
            "index": index,
            "distanceMiles": segment.get("distance"),
            "durationMinutes": _minutes(segment.get("duration")),
            "steps": steps,
        })
    return {
        "coordinates": feature.get("geometry", {}).get("coordinates") or [],
        "legs": legs,
        "distanceMiles": summary.get("distance"),
        "durationMinutes": _minutes(summary.get("duration")),
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


def _configured_provider():
    provider = os.getenv("ROUTING_PROVIDER", "aws_location").strip().lower()
    if provider == "aws_location":
        return AmazonLocationRoutesProvider()
    if provider == "openrouteservice":
        return OpenRouteServiceProvider()
    raise TravelTimeProviderError("ROUTING_PROVIDER must be aws_location or openrouteservice")


def get_road_route_matrix(points):
    return _configured_provider().calculate_matrix(points)


def get_road_route_directions(points, alternatives=2):
    routes = _configured_provider().directions(points, alternatives=alternatives)
    primary = dict(routes[0])
    primary["alternatives"] = routes[1:]
    return primary


def get_openrouteservice_matrix(points):
    return OpenRouteServiceProvider().calculate_matrix(points)


def get_openrouteservice_directions(points, alternatives=2):
    routes = OpenRouteServiceProvider().directions(points, alternatives=alternatives)
    primary = dict(routes[0])
    primary["alternatives"] = routes[1:]
    return primary
