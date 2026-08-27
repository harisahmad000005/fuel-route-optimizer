from dataclasses import dataclass

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344


@dataclass(frozen=True)
class Route:
    """
    Represents a calculated driving route.

    Attributes:
        distance_miles:
            Total driving distance of the route in miles.

        geometry:
            Ordered route coordinates as ``(longitude, latitude)`` pairs.
            The coordinates are returned by OSRM using GeoJSON format.
    """

    distance_miles: float
    geometry: list[tuple[float, float]]


class RoutingError(Exception):
    """
    Raised when the routing service cannot successfully calculate a route.

    This includes HTTP failures, invalid responses, routing errors returned
    by OSRM, or responses that do not contain a usable route.
    """


class RoutingService:
    """
    Calculates driving routes using the OSRM routing API.

    The service accepts the geographic coordinates of a start and finish
    location and requests a complete driving route from OSRM.

    OSRM returns:

        - route distance in meters
        - route geometry as GeoJSON coordinates

    The service converts the distance from meters to miles and normalizes
    the geometry into a list of ``(longitude, latitude)`` tuples.

    The rest of the application does not need to know anything about
    OSRM's API response structure.
    """

    BASE_URL = settings.OSRM_URL

    def get_route(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
    ) -> Route:
        """
        Calculate a driving route between two geographic coordinates.

        Args:
            start_lat:
                Latitude of the starting location.

            start_lon:
                Longitude of the starting location.

            end_lat:
                Latitude of the destination.

            end_lon:
                Longitude of the destination.

        Returns:
            A ``Route`` containing the total distance in miles and the
            complete ordered route geometry.

        Raises:
            RoutingError:
                If OSRM cannot be reached, returns invalid JSON, reports
                a routing failure, or does not provide a route.
        """

        url = f"{self.BASE_URL}/" f"{start_lon},{start_lat};" f"{end_lon},{end_lat}"

        params = {
            "overview": "full",
            "geometries": "geojson",
        }

        try:
            response = requests.get(
                url,
                params=params,
                timeout=15,
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            raise RoutingError("Unable to calculate route.") from exc

        try:
            data = response.json()

        except ValueError as exc:
            raise RoutingError("Routing service returned invalid JSON.") from exc

        if data.get("code") != "Ok":
            raise RoutingError(f"Routing failed: {data.get('code', 'Unknown error')}")

        routes = data.get("routes")

        if not routes:
            raise RoutingError("Routing service returned no routes.")

        route = routes[0]

        distance_meters = route["distance"]

        distance_miles = distance_meters / METERS_PER_MILE

        coordinates = route["geometry"]["coordinates"]

        geometry = [(lon, lat) for lon, lat in coordinates]

        return Route(
            distance_miles=distance_miles,
            geometry=geometry,
        )
