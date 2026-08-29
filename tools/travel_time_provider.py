"""Amazon Location Routes V2 road-network travel-time matrices."""

import os

import boto3


class TravelTimeProviderError(RuntimeError):
    pass


class AmazonLocationTravelTimeProvider:
    """Server-side IAM-authenticated adapter for `geo-routes` V2."""

    def __init__(self, *, region=None, client=None):
        self.region = region or os.getenv("AWS_LOCATION_REGION") or os.getenv("AWS_REGION", "us-east-1")
        self.client = client or boto3.client("geo-routes", region_name=self.region)

    def calculate_matrix(self, points):
        if not 2 <= len(points) <= 16:
            raise ValueError("Amazon Location matrix requires 2-16 points for this optimizer")
        positions = [{"Position": [float(point["lon"]), float(point["lat"])]} for point in points]
        try:
            response = self.client.calculate_route_matrix(
                Origins=positions,
                Destinations=positions,
                RoutingBoundary={"Geometry": {"AutoCircle": {"Margin": 10000, "MaxRadius": 190000}}},
                TravelMode="Car",
                OptimizeRoutingFor="FastestRoute",
                Traffic={"Usage": "IgnoreTrafficData"},
            )
        except Exception as exc:
            raise TravelTimeProviderError(f"Amazon Location route matrix failed: {exc}") from exc
        route_matrix = response.get("RouteMatrix")
        if not isinstance(route_matrix, list) or len(route_matrix) != len(points):
            raise TravelTimeProviderError("Amazon Location returned an incomplete route matrix")
        matrix = []
        for row_index, row in enumerate(route_matrix):
            if not isinstance(row, list) or len(row) != len(points):
                raise TravelTimeProviderError("Amazon Location returned an incomplete route-matrix row")
            converted = []
            for column_index, cell in enumerate(row):
                if cell.get("Error"):
                    raise TravelTimeProviderError(
                        f"Amazon Location could not route matrix cell {row_index},{column_index}: {cell['Error']}"
                    )
                duration = cell.get("Duration")
                if duration is None:
                    raise TravelTimeProviderError(
                        f"Amazon Location omitted duration for matrix cell {row_index},{column_index}"
                    )
                converted.append(round(float(duration) / 60, 3))
            matrix.append(converted)
        return matrix


def get_amazon_location_matrix(points):
    return AmazonLocationTravelTimeProvider().calculate_matrix(points)
