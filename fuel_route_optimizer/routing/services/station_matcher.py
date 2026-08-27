from dataclasses import dataclass

from pyproj import Transformer
from shapely.geometry import LineString, Point

from stations.models import FuelStation


METERS_PER_MILE = 1609.344
ROUTE_STATION_RADIUS_MILES = 5.0


@dataclass
class StationCandidate:
    """
    Represents a fuel station matched to a calculated route.

    Attributes:
        station:
            The original fuel station database object.

        distance_from_route_miles:
            The shortest distance from the station to the
            calculated route.

        distance_along_route_miles:
            The station's position along the route, measured
            in miles from the starting point.

    The optimizer uses distance_along_route_miles to determine
    the order in which stations are encountered.
    """

    station: FuelStation
    distance_from_route_miles: float
    distance_along_route_miles: float


class StationMatcher:
    """
    Finds fuel stations that are relevant to a driving route.

    The matching process uses two stages:

    1. Bounding-box filtering:
       Quickly retrieves only stations whose coordinates fall
       inside the geographic bounding box surrounding the route.

    2. Route-distance filtering:
       Calculates the actual distance from each candidate station
       to the route and keeps only stations within the configured
       ROUTE_STATION_RADIUS_MILES.

    Stations that pass both filters are projected onto the route
    so their position along the journey can be determined.

    The result is sorted by distance along the route, allowing
    the fuel optimizer to process stations in driving order.
    """

    def get_bounding_box_candidates(
        self,
        geometry: list[tuple[float, float]],
    ) -> list[FuelStation]:
        """
        Retrieve stations inside the route's geographic bounding box.

        The route geometry contains many longitude/latitude points.
        The minimum and maximum longitude and latitude values are
        used to create a rectangular search area around the route.

        This is an optimization step. Instead of calculating the
        geometric distance from every fuel station in the database,
        we first eliminate stations that are obviously nowhere
        near the route.

        Args:
            geometry:
                Route coordinates as (longitude, latitude) pairs.

        Returns:
            Fuel stations whose coordinates fall inside the
            route bounding box.

        Returns an empty list when the route geometry is empty.
        """

        if not geometry:
            return []

        longitudes = [point[0] for point in geometry]
        latitudes = [point[1] for point in geometry]

        min_lon = min(longitudes)
        max_lon = max(longitudes)
        min_lat = min(latitudes)
        max_lat = max(latitudes)

        return list(
            FuelStation.objects.filter(
                latitude__gte=min_lat,
                latitude__lte=max_lat,
                longitude__gte=min_lon,
                longitude__lte=max_lon,
                latitude__isnull=False,
                longitude__isnull=False,
            )
        )

    def match_stations_to_route(
        self,
        geometry: list[tuple[float, float]],
        stations: list[FuelStation],
    ) -> list[StationCandidate]:
        """
        Calculate how each station relates to the route.

        Each station is evaluated using two measurements:

        1. Distance from the route:
           The shortest distance between the station and the
           route geometry.

        2. Distance along the route:
           The station's projected position measured from the
           beginning of the route.

        Geographic coordinates are converted from EPSG:4326
        (longitude/latitude) to EPSG:3857 before performing
        geometric calculations. This allows Shapely to work
        with distances expressed in meters.

        Stations farther than ROUTE_STATION_RADIUS_MILES from
        the route are discarded.

        The remaining stations are sorted by their position
        along the route so that the optimizer sees them in
        the order the vehicle would encounter them.

        Args:
            geometry:
                Route coordinates as (longitude, latitude) pairs.

            stations:
                Candidate fuel stations obtained from the
                initial bounding-box search.

        Returns:
            A sorted list of StationCandidate objects.

        Returns an empty list when either the route geometry
        or station list is empty.
        """

        if not geometry or not stations:
            return []

        # Convert geographic coordinates into a projected
        # coordinate system where geometric distances can be
        # calculated in meters.
        transformer = Transformer.from_crs(
            "EPSG:4326",
            "EPSG:3857",
            always_xy=True,
        )

        projected_route = LineString(
            [
                transformer.transform(lon, lat)
                for lon, lat in geometry
            ]
        )

        candidates = []

        for station in stations:

            station_point = Point(
                transformer.transform(
                    float(station.longitude),
                    float(station.latitude),
                )
            )

            # Calculate the shortest distance between the
            # station and any point on the route.
            distance_meters = projected_route.distance(
                station_point
            )

            distance_from_route_miles = (
                distance_meters / METERS_PER_MILE
            )

            # Ignore stations that require too much detouring
            # from the calculated route.
            if distance_from_route_miles > ROUTE_STATION_RADIUS_MILES:
                continue

            # Project the station onto the route. The resulting
            # distance represents how far along the route the
            # station occurs.
            distance_along_route_miles = (
                projected_route.project(station_point)
                / METERS_PER_MILE
            )

            candidates.append(
                StationCandidate(
                    station=station,
                    distance_from_route_miles=distance_from_route_miles,
                    distance_along_route_miles=distance_along_route_miles,
                )
            )

        return sorted(
            candidates,
            key=lambda candidate: candidate.distance_along_route_miles,
        )