import hashlib
import json
from decimal import Decimal

from routing.models import RouteCache
from routing.services.fuel_optimizer import (
    FuelPlan,
    FuelStop,
)
from routing.services.station_prices import PricedStationCandidate


class RouteCacheService:
    """
    Handles storing and retrieving optimized routes.

    A cached route is considered valid only when the current
    fuel-price fingerprint matches the fingerprint stored with
    the cached result.
    """

    def build_price_fingerprint(
        self,
        stations: list[PricedStationCandidate],
    ) -> str:
        """
        Create a deterministic fingerprint from stations
        and their current prices.

        The fingerprint changes when:

        - a station price changes
        - a station is removed
        - a relevant station is added
        """

        price_data = sorted(
            (
                candidate.candidate.station.id,
                str(candidate.price),
            )
            for candidate in stations
        )

        serialized = json.dumps(
            price_data,
            separators=(",", ":"),
        )

        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get_route_cache(
        self,
        start_location: str,
        finish_location: str,
    ) -> RouteCache | None:
        """
        Retrieve the cached route for a start/end location.

        This only checks whether a cache entry exists.

        Price validity is checked later using the fingerprint.
        """

        return RouteCache.objects.filter(
            start_location=start_location,
            finish_location=finish_location,
        ).first()

    def get_valid_cache(
        self,
        start_location: str,
        finish_location: str,
        price_fingerprint: str,
    ) -> RouteCache | None:
        """
        Return the cached route only when its price fingerprint
        matches the current station prices.

        A stale cache entry is deleted.
        """

        cache = self.get_route_cache(
            start_location=start_location,
            finish_location=finish_location,
        )

        if cache is None:
            return None

        if cache.price_fingerprint != price_fingerprint:
            cache.delete()
            return None

        return cache

    def build_fuel_plan_from_cache(
        self,
        cache: RouteCache,
    ) -> FuelPlan:
        """
        Reconstruct a FuelPlan from the JSON stored in RouteCache.
        """

        stops = []

        for stop in cache.fuel_stops:
            stops.append(
                FuelStop(
                    station=self._build_priced_station_candidate(stop),
                    gallons=float(stop["gallons"]),
                    cost=Decimal(str(stop["cost"])),
                )
            )

        return FuelPlan(
            stops=stops,
            total_purchased_gallons=(cache.fuel_total_purchased_gallons),
            total_consumed_gallons=(cache.fuel_total_consumed_gallons),
            remaining_gallons=(cache.fuel_remaining_gallons),
            total_cost=cache.fuel_total_cost,
        )

    def _build_priced_station_candidate(
        self,
        stop: dict,
    ) -> PricedStationCandidate:
        """
        Reconstruct a PricedStationCandidate from cached
        station information.

        The actual database station is loaded so the resulting
        FuelPlan has the same structure as a freshly calculated
        plan.
        """

        from routing.services.station_matcher import (
            StationCandidate,
        )
        from stations.models import FuelStation

        station = FuelStation.objects.get(id=stop["station_id"])

        candidate = StationCandidate(
            station=station,
            distance_from_route_miles=float(stop["distance_from_route_miles"]),
            distance_along_route_miles=float(stop["distance_along_route_miles"]),
        )

        return PricedStationCandidate(
            candidate=candidate,
            price=Decimal(str(stop["price"])),
        )

    def save(
        self,
        start_location: str,
        finish_location: str,
        start_latitude: float,
        start_longitude: float,
        finish_latitude: float,
        finish_longitude: float,
        route_distance_miles: float,
        route_geometry: list,
        fuel_plan: FuelPlan,
        price_fingerprint: str,
    ) -> RouteCache:
        """
        Create or update a route cache entry.
        """

        fuel_stops = [
            {
                "station_id": stop.station.candidate.station.id,
                "station": stop.station.candidate.station.name,
                "city": stop.station.candidate.station.city,
                "state": stop.station.candidate.station.state,
                "price": str(stop.station.price),
                "distance_along_route_miles": (
                    stop.station.candidate.distance_along_route_miles
                ),
                "distance_from_route_miles": (
                    stop.station.candidate.distance_from_route_miles
                ),
                "gallons": stop.gallons,
                "cost": str(stop.cost),
            }
            for stop in fuel_plan.stops
        ]

        cache, _ = RouteCache.objects.update_or_create(
            start_location=start_location,
            finish_location=finish_location,
            defaults={
                "start_latitude": start_latitude,
                "start_longitude": start_longitude,
                "finish_latitude": finish_latitude,
                "finish_longitude": finish_longitude,
                "route_distance_miles": route_distance_miles,
                "route_geometry": route_geometry,
                "fuel_total_purchased_gallons": (fuel_plan.total_purchased_gallons),
                "fuel_total_consumed_gallons": (fuel_plan.total_consumed_gallons),
                "fuel_remaining_gallons": (fuel_plan.remaining_gallons),
                "fuel_total_cost": fuel_plan.total_cost,
                "fuel_stops": fuel_stops,
                "price_fingerprint": price_fingerprint,
            },
        )

        return cache
