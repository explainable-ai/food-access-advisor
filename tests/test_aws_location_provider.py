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
            "Distance": 1609.344,
            "Duration": 600,
            "Legs": [{
                "Distance": 1609.344,
                "Duration": 600,
                "Geometry": {"LineString": [[-89.2, 37.0], [-89.1, 37.1]]},
                "TravelSteps": [{"Instruction": "Continue", "Distance": 804.672, "Duration": 300}],
            }],
        }]}


def test_amazon_location_matrix_uses_signed_routes_v2_shape():
    client = RoutesClient()
    provider = AmazonLocationRoutesProvider(client=client)
    points = [{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}]

    assert provider.calculate_matrix(points) == [[0, 2], [3, 0]]
    assert client.matrix_request["RoutingBoundary"] == {"Unbounded": True}
    assert client.matrix_request["Origins"][0]["Position"] == [-89.2, 37.0]
    assert client.matrix_request["TravelMode"] == "Car"


def test_amazon_location_directions_keep_real_geometry_and_units():
    client = RoutesClient()
    provider = AmazonLocationRoutesProvider(client=client)
    routes = provider.directions([{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}])

    assert routes[0]["coordinates"] == [[-89.2, 37.0], [-89.1, 37.1]]
    assert routes[0]["distanceMiles"] == 1
    assert routes[0]["durationMinutes"] == 10
    assert routes[0]["legs"][0]["steps"][0]["instruction"] == "Continue"
    assert client.routes_request["LegGeometryFormat"] == "Simple"


def test_amazon_location_matrix_fails_closed_on_unroutable_pair():
    client = RoutesClient()
    client.calculate_route_matrix = lambda **kwargs: {
        "RouteMatrix": [[{"Duration": 0}, {"Error": "NoRoute"}], [{"Duration": 2}, {"Duration": 0}]]
    }
    with pytest.raises(TravelTimeProviderError, match="every stop pair"):
        AmazonLocationRoutesProvider(client=client).calculate_matrix(
            [{"lat": 37.0, "lon": -89.2}, {"lat": 37.1, "lon": -89.1}]
        )
