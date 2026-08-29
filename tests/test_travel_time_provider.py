import pytest

from tools.travel_time_provider import AmazonLocationTravelTimeProvider, TravelTimeProviderError


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    def calculate_route_matrix(self, **kwargs):
        self.request = kwargs
        return self.response


def test_amazon_location_matrix_uses_lon_lat_and_converts_seconds():
    client = FakeClient({"RouteMatrix": [[{"Duration": 0}, {"Duration": 600}],
                                                   [{"Duration": 720}, {"Duration": 0}]]})
    provider = AmazonLocationTravelTimeProvider(client=client)
    matrix = provider.calculate_matrix([{"lat": 37, "lon": -89}, {"lat": 38, "lon": -88}])
    assert matrix == [[0, 10], [12, 0]]
    assert client.request["Origins"][0]["Position"] == [-89.0, 37.0]
    assert client.request["TravelMode"] == "Car"
    assert client.request["Traffic"]["Usage"] == "IgnoreTrafficData"


def test_amazon_location_matrix_fails_closed_on_cell_error():
    response = {"RouteMatrix": [[{"Duration": 0}, {"Error": "NoRoute"}],
                                [{"Duration": 1}, {"Duration": 0}]]}
    with pytest.raises(TravelTimeProviderError, match="NoRoute"):
        AmazonLocationTravelTimeProvider(client=FakeClient(response)).calculate_matrix(
            [{"lat": 37, "lon": -89}, {"lat": 38, "lon": -88}])


def test_amazon_location_matrix_rejects_incomplete_response():
    with pytest.raises(TravelTimeProviderError, match="incomplete"):
        AmazonLocationTravelTimeProvider(client=FakeClient({"RouteMatrix": []})).calculate_matrix(
            [{"lat": 37, "lon": -89}, {"lat": 38, "lon": -88}])
