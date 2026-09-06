import pytest

from tools.travel_time_provider import OpenRouteServiceProvider, TravelTimeProviderError


class Response:
    def __init__(self, payload):
        self.payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self.payload


class Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.payload)


class SequencedSession(Session):
    def __init__(self, payloads):
        super().__init__(None)
        self.payloads = iter(payloads)

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(next(self.payloads))


def test_matrix_converts_seconds_to_minutes():
    session = Session({"durations": [[0, 120], [180, 0]]})
    provider = OpenRouteServiceProvider(api_key="test", session=session)
    matrix = provider.calculate_matrix([{"lat": 1, "lon": 2}, {"lat": 3, "lon": 4}])
    assert matrix == [[0, 2], [3, 0]]
    assert session.calls[0][0].endswith("/v2/matrix/driving-car")


def test_directions_parses_geojson_and_alternatives():
    feature = {
        "geometry": {"coordinates": [[-89.2, 37.0], [-89.1, 37.1]]},
        "properties": {"summary": {"distance": 8.5, "duration": 600}, "segments": []},
    }
    provider = OpenRouteServiceProvider(api_key="test", session=Session({"features": [feature, feature]}))
    routes = provider.directions([{"lat": 37, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}])
    assert len(routes) == 2
    assert routes[0]["durationMinutes"] == 10
    assert provider.session.calls[0][1]["json"]["radiuses"] == [5000, 5000]


def test_waypoint_route_returns_color_coded_route_choices():
    def feature(coordinates, distance):
        return {
            "geometry": {"coordinates": coordinates},
            "properties": {"summary": {"distance": distance, "duration": 600}, "segments": []},
        }

    primary = feature([[-89.2, 37.0], [-89.15, 37.05], [-89.2, 37.0]], 9)
    shortest = feature([[-89.2, 37.0], [-89.14, 37.04], [-89.2, 37.0]], 8)
    session = SequencedSession([
        {"features": [primary]},
        {"features": [shortest]},
    ])
    provider = OpenRouteServiceProvider(api_key="test", session=session)
    routes = provider.directions([
        {"lat": 37.0, "lon": -89.2},
        {"lat": 37.05, "lon": -89.15},
        {"lat": 37.1, "lon": -89.1},
        {"lat": 37.0, "lon": -89.2},
    ])

    assert len(routes) == 2
    assert session.calls[1][1]["json"]["preference"] == "shortest"
    assert all(call[1]["json"]["radiuses"] == [5000] * 4 for call in session.calls)
    assert all(
        call[1]["json"]["coordinates"]
        == [[-89.2, 37.0], [-89.15, 37.05], [-89.1, 37.1], [-89.2, 37.0]]
        for call in session.calls
    )


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    with pytest.raises(TravelTimeProviderError):
        OpenRouteServiceProvider()
