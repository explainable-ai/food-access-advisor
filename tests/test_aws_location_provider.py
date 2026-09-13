import pytest

from tools.travel_time_provider import AmazonLocationRoutesProvider, TravelTimeProviderError


class RoutesClient:
    def __init__(self):
        self.matrix_request = None
        self.routes_request = None

    def calculate_route_matrix(self, **kwargs):
        self.matrix_request = kwargs
        return {
            "ErrorCount": 0,
            "RouteMatrix": [
                [{"Distance": 0, "Duration": 0}, {"Distance": 1609, "Duration": 120}],
                [{"Distance": 1609, "Duration": 180}, {"Distance": 0, "Duration": 0}],
            ],
        }

    def calculate_routes(self, **kwargs):
        self.routes_request = kwargs
        return {"Routes": [{
            "Summary": {"Distance": 1609.344, "Duration": 600},
            "Legs": [{
                "Summary": {"Distance": 1609.344, "Duration": 600},
                "Geometry": {"LineString": [[-89.2, 37.0], [-89.1, 37.1]]},
                "VehicleLegDetails": {
                    "TravelSteps": [{"Instruction": "Continue", "Distance": 804.672, "Duration": 300}],
                },
            }],
        }]}


def test_amazon_location_matrix_uses_live_traffic_and_signed_routes_v2_shape(monkeypatch):
    monkeypatch.delenv("ROUTING_DEPART_NOW", raising=False)
    monkeypatch.delenv("ROUTING_TRAVEL_MODE", raising=False)
    client = RoutesClient()
    provider = AmazonLocationRoutesProvider(client=client)
    points = [{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}]

    assert provider.calculate_matrix(points) == [[0, 2], [3, 0]]
    assert client.matrix_request["RoutingBoundary"] == {"Unbounded": True}
    assert client.matrix_request["Origins"][0]["Position"] == [-89.2, 37.0]
    assert client.matrix_request["TravelMode"] == "Car"
    assert client.matrix_request["Traffic"] == {"Usage": "UseTrafficData"}
    assert client.matrix_request["DepartNow"] is True


def test_amazon_location_directions_keep_real_geometry_units_and_live_departure(monkeypatch):
    monkeypatch.delenv("ROUTING_DEPART_NOW", raising=False)
    monkeypatch.delenv("ROUTING_TRAVEL_MODE", raising=False)
    client = RoutesClient()
    provider = AmazonLocationRoutesProvider(client=client)
    routes = provider.directions([{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}])

    assert routes[0]["coordinates"] == [[-89.2, 37.0], [-89.1, 37.1]]
    assert routes[0]["distanceMiles"] == 1
    assert routes[0]["durationMinutes"] == 10
    assert routes[0]["legs"][0]["steps"][0]["instruction"] == "Continue"
    assert client.routes_request["LegGeometryFormat"] == "Simple"
    assert client.routes_request["Traffic"] == {"Usage": "UseTrafficData"}
    assert client.routes_request["DepartNow"] is True


def test_amazon_location_supports_truck_mode(monkeypatch):
    monkeypatch.setenv("ROUTING_TRAVEL_MODE", "Truck")
    client = RoutesClient()
    provider = AmazonLocationRoutesProvider(client=client)
    provider.calculate_matrix([{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}])

    assert client.matrix_request["TravelMode"] == "Truck"


def test_live_departure_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ROUTING_DEPART_NOW", "false")
    client = RoutesClient()
    provider = AmazonLocationRoutesProvider(client=client)
    provider.calculate_matrix([{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}])

    assert "DepartNow" not in client.matrix_request


def test_invalid_travel_mode_is_explicit(monkeypatch):
    monkeypatch.setenv("ROUTING_TRAVEL_MODE", "Bicycle")
    with pytest.raises(TravelTimeProviderError, match="Car or Truck"):
        AmazonLocationRoutesProvider(client=RoutesClient())


def test_amazon_location_matrix_fails_closed_on_unroutable_pair(monkeypatch):
    monkeypatch.delenv("ROUTING_TRAVEL_MODE", raising=False)
    client = RoutesClient()
    client.calculate_route_matrix = lambda **kwargs: {
        "RouteMatrix": [[{"Duration": 0}, {"Error": "NoRoute"}], [{"Duration": 2}, {"Duration": 0}]]
    }
    with pytest.raises(TravelTimeProviderError, match="every stop pair"):
        AmazonLocationRoutesProvider(client=client).calculate_matrix(
            [{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}]
        )
