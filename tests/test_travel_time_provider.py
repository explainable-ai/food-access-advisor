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


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv("OPENROUTESERVICE_API_KEY", raising=False)
    with pytest.raises(TravelTimeProviderError):
        OpenRouteServiceProvider()
