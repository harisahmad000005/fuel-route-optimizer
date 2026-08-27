from dataclasses import dataclass

from routing.services.fuel_optimizer import (
    FuelOptimizer,
    FuelPlan,
)
from routing.services.route_cache import (
    RouteCacheService,
)
from routing.services.routing import (
    Route,
    RoutingService,
)
from routing.services.station_matcher import (
    StationMatcher,
)
from routing.services.station_prices import (
    PricedStationCandidate,
    StationPriceService,
)


@dataclass
class RouteOptimizationResult:
    """
    Contains the complete result of a route optimization request.

    Attributes:
        route:
            The calculated route, including total distance and
            route geometry.

        stations:
            Fuel stations that were matched to the route and
            enriched with their latest available prices.

        fuel_plan:
            The fueling strategy selected by the fuel optimizer,
            including recommended stops, purchased fuel, and total cost.
    """

    route: Route
    stations: list[PricedStationCandidate]
    fuel_plan: FuelPlan


class RouteOptimizer:
    """
    Orchestrates the complete route optimization workflow.

    This service coordinates the individual components responsible for:

        1. Checking the route cache.
        2. Calculating the route when it is not cached.
        3. Finding fuel stations near the route.
        4. Projecting stations onto the route.
        5. Attaching the latest fuel prices.
        6. Detecting changes in station prices.
        7. Calculating the optimal fueling strategy.
        8. Caching the resulting route and fuel plan.

    The class intentionally keeps these responsibilities separate
    by delegating each task to a dedicated service.

    The high-level flow is:

        Location
            ↓
        Route / Route Cache
            ↓
        Station Matching
            ↓
        Station Prices
            ↓
        Price Fingerprint
            ↓
        Cached Fuel Plan OR Fuel Optimizer
            ↓
        RouteOptimizationResult
    """

    def __init__(self):
        """
        Initialize the services used by the route optimization pipeline.
        """

        self.routing_service = RoutingService()
        self.station_matcher = StationMatcher()
        self.station_price_service = StationPriceService()
        self.fuel_optimizer = FuelOptimizer()
        self.route_cache_service = RouteCacheService()

    def optimize(
        self,
        start_lat: float,
        start_lon: float,
        end_lat: float,
        end_lon: float,
        start_location: str = "",
        finish_location: str = "",
    ) -> RouteOptimizationResult:
        """
        Calculate an optimized route and fueling plan.

        The method first checks whether the requested route exists
        in the cache.

        If a cached route exists, its route geometry and distance
        are reused instead of requesting the route again from the
        routing provider.

        Fuel stations are then:

            1. Retrieved using a geographic bounding box around
               the route.
            2. Matched to the actual route geometry.
            3. Ordered by their position along the route.
            4. Enriched with their latest fuel prices.

        A fingerprint of the current station prices is generated.
        If both the route and its station-price fingerprint match
        the cached data, the previously calculated fuel plan is
        reused.

        Otherwise, the FuelOptimizer calculates a new fueling plan.

        Finally, the latest route and optimization result are saved
        to the cache.

        This means route calculation and fuel optimization are
        independently cache-aware:

            - Cached route + unchanged prices:
                reuse everything.

            - Cached route + changed prices:
                reuse route, recalculate fuel plan.

            - No cached route:
                calculate route and fuel plan, then cache both.

        Args:
            start_lat:
                Latitude of the starting location.

            start_lon:
                Longitude of the starting location.

            end_lat:
                Latitude of the destination.

            end_lon:
                Longitude of the destination.

            start_location:
                Human-readable starting location used as part of
                the route cache key.

            finish_location:
                Human-readable destination used as part of the
                route cache key.

        Returns:
            RouteOptimizationResult:
                The calculated route, matched and priced stations,
                and the optimal fueling plan.

        Raises:
            ValueError:
                May be raised by the routing, station matching,
                or fuel optimization services when the requested
                route cannot be processed or the destination cannot
                be reached with the available fueling options.
        """

        # 1. Check whether this route already exists in the cache.
        #
        # The cache is keyed using the human-readable start and
        # finish locations.
        cached_route = self.route_cache_service.get_route_cache(
            start_location=start_location,
            finish_location=finish_location,
        )

        # 2. Reuse the cached route when available.
        #
        # If the route is not cached, request a fresh route from
        # the routing provider.
        if cached_route is not None:

            route = Route(
                distance_miles=cached_route.route_distance_miles,
                geometry=[tuple(point) for point in cached_route.route_geometry],
            )

        else:

            route = self.routing_service.get_route(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
            )

        # 3. Perform a broad geographic filtering step.
        #
        # This avoids checking every fuel station in the database
        # against every point in the route geometry.
        candidates = self.station_matcher.get_bounding_box_candidates(
            route.geometry
        )

        # 4. Match the remaining candidates to the actual route.
        #
        # This determines how far each station is from the route
        # and where the station occurs along the route.
        matched = self.station_matcher.match_stations_to_route(
            route.geometry,
            candidates,
        )

        # 5. Attach the latest available fuel price to each
        # matched station.
        priced = self.station_price_service.attach_latest_prices(
            matched
        )

        # 6. Generate a fingerprint representing the current
        # station prices.
        #
        # This allows us to determine whether a previously cached
        # fuel plan is still valid.
        price_fingerprint = (
            self.route_cache_service.build_price_fingerprint(
                priced
            )
        )

        # 7. If both the route and station prices are unchanged,
        # reuse the previously calculated fuel plan.
        #
        # There is no need to run the optimization algorithm again
        # when the inputs that affect the result have not changed.
        if cached_route is not None:

            if cached_route.price_fingerprint == price_fingerprint:

                fuel_plan = (
                    self.route_cache_service.build_fuel_plan_from_cache(
                        cached_route
                    )
                )

                return RouteOptimizationResult(
                    route=route,
                    stations=priced,
                    fuel_plan=fuel_plan,
                )

        # 8. Either the route is new or the station prices have
        # changed.
        #
        # In either case, calculate a fresh fueling strategy.
        fuel_plan = self.fuel_optimizer.optimize(
            route_distance_miles=route.distance_miles,
            candidates=priced,
        )

        # 9. Cache the route, fuel plan, and current station-price
        # fingerprint so that future requests can avoid repeating
        # expensive calculations when the inputs have not changed.
        self.route_cache_service.save(
            start_location=start_location,
            finish_location=finish_location,
            start_latitude=start_lat,
            start_longitude=start_lon,
            finish_latitude=end_lat,
            finish_longitude=end_lon,
            route_distance_miles=route.distance_miles,
            route_geometry=route.geometry,
            fuel_plan=fuel_plan,
            price_fingerprint=price_fingerprint,
        )

        return RouteOptimizationResult(
            route=route,
            stations=priced,
            fuel_plan=fuel_plan,
        )