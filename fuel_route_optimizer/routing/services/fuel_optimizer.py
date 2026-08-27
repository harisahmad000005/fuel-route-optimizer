from dataclasses import dataclass
from decimal import Decimal

from routing.constants import (
    VEHICLE_MPG,
    VEHICLE_TANK_CAPACITY_GALLONS,
    VEHICLE_STARTING_FUEL_GALLONS,
)
from routing.services.station_prices import PricedStationCandidate


@dataclass
class FuelStop:
    """
    Represents one actual fueling decision.

    Attributes:
        station:
            The route-matched station together with its fuel price.

        gallons:
            Number of gallons purchased at the station.

        cost:
            Total cost of the fuel purchased at this station.
    """

    station: PricedStationCandidate
    gallons: float
    cost: Decimal


@dataclass
class FuelPlan:
    """
    Represents the complete fueling plan for a route.

    The plan contains both the individual fueling stops and
    aggregate fuel metrics for the entire journey.

    Attributes:
        stops:
            Ordered list of stations where fuel is purchased.

        total_purchased_gallons:
            Total amount of fuel purchased during the journey.

        total_consumed_gallons:
            Total fuel required to drive the complete route.

        remaining_gallons:
            Fuel remaining when the vehicle reaches the destination.

        total_cost:
            Total cost of all fuel purchased.
    """

    stops: list[FuelStop]
    total_purchased_gallons: float
    total_consumed_gallons: float
    remaining_gallons: float
    total_cost: Decimal


class FuelOptimizer:
    """
    Calculates a cost-effective fueling plan for a route.

    The optimizer models the vehicle as moving along a one-dimensional
    route where each fuel station has:

        - a position along the route
        - a fuel price
        - a distance from the route

    The optimizer processes stations in route order.

    Vehicle assumptions:

        - The vehicle starts with a full tank.
        - Fuel economy is constant.
        - Tank capacity is fixed.
        - Fuel prices are fixed for the duration of the calculation.
        - Refueling fills the tank completely.
        - Partial refueling is not supported.

    Core decision strategy:

        1. Drive to the next reachable station.
        2. Check whether the destination is already reachable.
        3. If a cheaper reachable station exists ahead, skip the
           current station and preserve the existing fuel.
        4. Otherwise, fill the tank completely at the current station.
        5. Continue until the destination can be reached.

    This approach avoids simply selecting the cheapest station.
    Instead, it considers both fuel price and whether the vehicle
    can physically reach the cheaper station.

    Example:

        Current station = $3.20
        Future station  = $2.80

    If the future station is reachable with the current fuel,
    buying at $3.20 is unnecessary, so the optimizer waits.

    If the cheaper station is too far away, the vehicle must refuel
    at the current station to avoid becoming stranded.
    """

    def optimize(
        self,
        route_distance_miles: float,
        candidates: list[PricedStationCandidate],
    ) -> FuelPlan:
        """
        Calculate the fueling plan for the complete route.

        The vehicle starts with VEHICLE_STARTING_FUEL_GALLONS.

        Fuel consumption is calculated as:

            consumed_fuel = route_distance / VEHICLE_MPG

        The final fuel balance is calculated as:

            remaining_fuel =
                starting_fuel
                + purchased_fuel
                - consumed_fuel

        The method first handles routes that require no refueling.
        For longer routes, it processes stations in order and makes
        a fuel decision at each reachable station.

        Raises:
            ValueError:
                If the route cannot be completed with the available
                stations and vehicle range.
        """

        self._validate_inputs(route_distance_miles)

        # --------------------------------------------------
        # Empty route.
        # --------------------------------------------------

        if route_distance_miles == 0:
            return self._empty_plan(
                route_distance_miles=route_distance_miles,
            )

        # --------------------------------------------------
        # First optimization:
        #
        # If the destination is already reachable with the
        # starting fuel, there is no reason to visit a fuel
        # station.
        # --------------------------------------------------

        maximum_initial_range = VEHICLE_STARTING_FUEL_GALLONS * VEHICLE_MPG

        if route_distance_miles <= maximum_initial_range:
            return self._empty_plan(
                route_distance_miles=route_distance_miles,
            )

        # --------------------------------------------------
        # Prepare stations by removing invalid candidates
        # and ordering them by their position along the route.
        # --------------------------------------------------

        stations = self._prepare_candidates(
            route_distance_miles,
            candidates,
        )

        current_fuel = VEHICLE_STARTING_FUEL_GALLONS
        current_position = 0.0

        stops: list[FuelStop] = []

        total_purchased_gallons = 0.0
        total_cost = Decimal("0")

        # --------------------------------------------------
        # Process stations in the order the vehicle encounters
        # them along the route.
        # --------------------------------------------------

        for index, station in enumerate(stations):

            station_position = station.candidate.distance_along_route_miles

            distance_to_station = station_position - current_position

            if distance_to_station < 0:
                continue

            fuel_required = distance_to_station / VEHICLE_MPG

            # --------------------------------------------------
            # The current station cannot be reached.
            #
            # Because stations are sorted by route position,
            # later stations are even farther away and therefore
            # cannot be reached either.
            # --------------------------------------------------

            if fuel_required > current_fuel:
                break

            # --------------------------------------------------
            # Drive to the station and consume the required fuel.
            # --------------------------------------------------

            current_fuel -= fuel_required
            current_position = station_position

            # --------------------------------------------------
            # Check the destination before considering another
            # fuel purchase.
            #
            # This is important because if the vehicle can already
            # reach the destination, purchasing additional fuel
            # would increase the cost without providing any benefit.
            # --------------------------------------------------

            distance_to_destination = route_distance_miles - current_position

            fuel_required_to_destination = distance_to_destination / VEHICLE_MPG

            if current_fuel >= fuel_required_to_destination:
                break

            # --------------------------------------------------
            # Look ahead for a cheaper reachable station.
            #
            # If one exists, buying fuel here would be unnecessary
            # because we can reach a lower-priced station using
            # the fuel already in the tank.
            # --------------------------------------------------

            cheaper_station = self._find_cheaper_reachable_station(
                current_index=index,
                current_station=station,
                stations=stations,
                current_fuel=current_fuel,
            )

            # --------------------------------------------------
            # A cheaper station is reachable.
            #
            # Skip the current station and continue toward the
            # cheaper station.
            # --------------------------------------------------

            if cheaper_station is not None:
                continue

            # --------------------------------------------------
            # No cheaper reachable station exists.
            #
            # We must refuel here to extend the vehicle's range.
            #
            # Partial refueling is intentionally not supported,
            # so the tank is filled completely.
            # --------------------------------------------------

            gallons_to_fill = VEHICLE_TANK_CAPACITY_GALLONS - current_fuel

            if gallons_to_fill <= 0:
                continue

            cost = station.price * Decimal(str(gallons_to_fill))

            stops.append(
                FuelStop(
                    station=station,
                    gallons=gallons_to_fill,
                    cost=cost,
                )
            )

            total_purchased_gallons += gallons_to_fill
            total_cost += cost

            current_fuel = VEHICLE_TANK_CAPACITY_GALLONS

        # --------------------------------------------------
        # Final reachability check.
        #
        # After processing all reachable stations, verify that
        # the remaining fuel is enough to reach the destination.
        # --------------------------------------------------

        remaining_distance = route_distance_miles - current_position

        fuel_required = remaining_distance / VEHICLE_MPG

        if current_fuel < fuel_required:
            raise ValueError(
                "Destination cannot be reached with " "the available fuel stations."
            )

        # --------------------------------------------------
        # Calculate journey-wide fuel statistics.
        # --------------------------------------------------

        total_consumed_gallons = route_distance_miles / VEHICLE_MPG

        remaining_gallons = (
            VEHICLE_STARTING_FUEL_GALLONS
            + total_purchased_gallons
            - total_consumed_gallons
        )

        return FuelPlan(
            stops=stops,
            total_purchased_gallons=(total_purchased_gallons),
            total_consumed_gallons=(total_consumed_gallons),
            remaining_gallons=max(
                0.0,
                remaining_gallons,
            ),
            total_cost=total_cost,
        )

    def _find_cheaper_reachable_station(
        self,
        current_index: int,
        current_station: PricedStationCandidate,
        stations: list[PricedStationCandidate],
        current_fuel: float,
    ) -> PricedStationCandidate | None:
        """
        Find the first cheaper station that can be reached
        using the fuel currently in the tank.

        The method only looks forward along the route.

        A station qualifies when:

            station.price < current_station.price

        and:

            distance_to_station <= current_fuel * VEHICLE_MPG

        Because stations are sorted by route position, once a
        station is outside the vehicle's current range, all
        subsequent stations are also unreachable.

        Returns:
            The first cheaper reachable station, or None when
            no such station exists.
        """

        current_position = current_station.candidate.distance_along_route_miles

        maximum_distance = current_fuel * VEHICLE_MPG

        for station in stations[current_index + 1 :]:

            station_position = station.candidate.distance_along_route_miles

            distance = station_position - current_position

            if distance < 0:
                continue

            # Stations are ordered by route position.
            # Therefore every later station is even farther away.
            if distance > maximum_distance:
                break

            if station.price < current_station.price:
                return station

        return None

    def _prepare_candidates(
        self,
        route_distance_miles: float,
        candidates: list[PricedStationCandidate],
    ) -> list[PricedStationCandidate]:
        """
        Filter and order station candidates before optimization.

        A candidate is considered valid when:

            - It is after the route start.
            - It is before the destination.
            - Its fuel price is not negative.

        Stations are sorted by distance along the route so the
        optimizer can process them in the same order the vehicle
        encounters them.

        Returns:
            A sorted list of valid priced station candidates.
        """

        return sorted(
            [
                candidate
                for candidate in candidates
                if (
                    0
                    < candidate.candidate.distance_along_route_miles
                    < route_distance_miles
                    and candidate.price >= 0
                )
            ],
            key=lambda candidate: (candidate.candidate.distance_along_route_miles),
        )

    def _empty_plan(
        self,
        route_distance_miles: float = 0.0,
    ) -> FuelPlan:
        """
        Create a fuel plan when no purchases are required.

        This is used when:

            - The route distance is zero.
            - The destination can be reached using the
              vehicle's starting fuel.

        Even when no fuel is purchased, the method still calculates
        consumed and remaining fuel so the API response provides
        a complete fuel summary.
        """

        total_consumed_gallons = route_distance_miles / VEHICLE_MPG

        remaining_gallons = max(
            0.0,
            VEHICLE_STARTING_FUEL_GALLONS - total_consumed_gallons,
        )

        return FuelPlan(
            stops=[],
            total_purchased_gallons=0.0,
            total_consumed_gallons=(total_consumed_gallons),
            remaining_gallons=remaining_gallons,
            total_cost=Decimal("0"),
        )

    def _validate_inputs(
        self,
        route_distance_miles: float,
    ) -> None:
        """
        Validate the vehicle configuration and route distance.

        Validation ensures that:

            - Route distance is not negative.
            - MPG is greater than zero.
            - Tank capacity is greater than zero.
            - Starting fuel is not negative.
            - Starting fuel does not exceed tank capacity.

        Raises:
            ValueError:
                If any vehicle or route constraint is invalid.
        """

        if route_distance_miles < 0:
            raise ValueError("Route distance cannot be negative.")

        if VEHICLE_MPG <= 0:
            raise ValueError("Vehicle MPG must be greater than zero.")

        if VEHICLE_TANK_CAPACITY_GALLONS <= 0:
            raise ValueError("Vehicle tank capacity must be greater than zero.")

        if VEHICLE_STARTING_FUEL_GALLONS < 0:
            raise ValueError("Starting fuel cannot be negative.")

        if VEHICLE_STARTING_FUEL_GALLONS > VEHICLE_TANK_CAPACITY_GALLONS:
            raise ValueError("Starting fuel cannot exceed tank capacity.")
