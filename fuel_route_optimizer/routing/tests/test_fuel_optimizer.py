from dataclasses import dataclass
from decimal import Decimal

from django.test import SimpleTestCase

from routing.services.fuel_optimizer import FuelOptimizer
from routing.services.station_matcher import StationCandidate
from routing.services.station_prices import PricedStationCandidate


@dataclass
class FakeStation:
    """
    Lightweight station object used by optimizer unit tests.
    """

    name: str


class FuelOptimizerTest(SimpleTestCase):

    def create_station(
        self,
        name: str,
        position: float,
        price: str,
    ) -> PricedStationCandidate:
        """
        Create an in-memory priced station candidate.

        The optimizer only needs basic station metadata,
        so database objects are not required.
        """

        station = FakeStation(
            name=name,
        )

        station_candidate = StationCandidate(
            station=station,
            distance_from_route_miles=0.5,
            distance_along_route_miles=position,
        )

        return PricedStationCandidate(
            candidate=station_candidate,
            price=Decimal(price),
        )

    # ==========================================================
    # BASIC ROUTE TESTS
    # ==========================================================

    def test_zero_distance_route(self):
        """
        A zero-mile route requires no fuel.
        """

        plan = FuelOptimizer().optimize(
            route_distance_miles=0,
            candidates=[],
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_gallons,
            0.0,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    def test_destination_reachable_with_starting_full_tank(self):
        """
        The vehicle starts with 50 gallons.

        At 10 MPG:

            50 × 10 = 500 miles

        Therefore a 400-mile route requires no fuel purchase.
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "2.50",
            ),
            self.create_station(
                "Station B",
                250,
                "3.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=400,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_gallons,
            0.0,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    def test_exact_maximum_range_is_reachable(self):
        """
        50 gallons × 10 MPG = 500 miles.

        Exactly 500 miles is reachable without refueling.
        """

        plan = FuelOptimizer().optimize(
            route_distance_miles=500,
            candidates=[],
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_gallons,
            0.0,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    # ==========================================================
    # DESTINATION REACHABILITY
    # ==========================================================

    def test_does_not_refuel_when_destination_is_reachable_from_station(
        self,
    ):
        """
        The vehicle reaches Station A with enough fuel to
        reach the destination.

        Route:

            Start ---- A ---------------- Destination
                     100                  400

        Starting fuel:

            50 gallons

        Fuel consumed to reach A:

            100 / 10 = 10 gallons

        Fuel remaining at A:

            50 - 10 = 40 gallons

        A -> destination:

            300 miles

        Fuel required:

            300 / 10 = 30 gallons

        Since 40 gallons are available, the destination
        is already reachable.

        Therefore A must NOT be used for refueling, even
        though it has a cheap price.
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=400,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_gallons,
            0.0,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    def test_does_not_refuel_when_only_five_miles_remain(self):
        """
        The vehicle is near the destination.

        If the remaining fuel can cover the final 5 miles,
        the optimizer should continue to the destination.

        It must not stop simply because a fuel station is
        nearby.
        """

        candidates = [
            self.create_station(
                "Station Near Destination",
                495,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=500,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_gallons,
            0.0,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    def test_cheaper_station_should_not_be_used_when_starting_fuel_is_enough(
        self,
    ):
        """
        A cheaper station does not matter if the destination
        can already be reached with the starting fuel.
        """

        candidates = [
            self.create_station(
                "Expensive Station",
                100,
                "3.00",
            ),
            self.create_station(
                "Cheap Station",
                300,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=400,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    # ==========================================================
    # FULL-TANK REFUELING
    # ==========================================================

    def test_refueling_always_fills_tank_completely(self):
        """
        Partial refueling is intentionally disabled.

        Route:

            Start ---- A ---------------- B ---- Destination
                     100                550       700

        Starting fuel:

            50 gallons

        At A:

            50 - 10 = 40 gallons

        A -> B:

            450 miles / 10 MPG = 45 gallons

        Therefore B cannot be reached with the existing
        40 gallons.

        Since partial refueling is disabled, A fills the
        tank completely:

            50 - 40 = 10 gallons purchased.

        Cost:

            10 × $2.00 = $20.00

        At B:

            50 - 45 = 5 gallons remain.

        B -> destination:

            150 miles / 10 MPG = 15 gallons

        Therefore B must fill the tank:

            50 - 5 = 45 gallons

        Cost:

            45 × $1.00 = $45.00

        Total:

            55 gallons
            $65.00
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "2.00",
            ),
            self.create_station(
                "Station B",
                550,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=700,
            candidates=candidates,
        )

        self.assertEqual(
            len(plan.stops),
            2,
        )

        first_stop = plan.stops[0]
        second_stop = plan.stops[1]

        self.assertEqual(
            first_stop.station.candidate.station.name,
            "Station A",
        )

        self.assertEqual(
            first_stop.station.price,
            Decimal("2.00"),
        )

        self.assertAlmostEqual(
            first_stop.gallons,
            10.0,
            places=6,
        )

        self.assertEqual(
            first_stop.cost,
            Decimal("20.00"),
        )

        self.assertEqual(
            second_stop.station.candidate.station.name,
            "Station B",
        )

        self.assertEqual(
            second_stop.station.price,
            Decimal("1.00"),
        )

        self.assertAlmostEqual(
            second_stop.gallons,
            45.0,
            places=6,
        )

        self.assertEqual(
            second_stop.cost,
            Decimal("45.00"),
        )

        self.assertAlmostEqual(
            plan.total_gallons,
            55.0,
            places=6,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("65.00"),
        )

    # ==========================================================
    # CHEAPER STATION LOOK-AHEAD
    # ==========================================================

    def test_uses_current_station_when_cheaper_station_is_out_of_range(
        self,
    ):
        """
        A cheaper station exists, but it is too far away.

        Therefore the current station must be used.

        Route:

            Start ---- A ---------------- B ---- Destination
                     100                550       700

        A = $2.00
        B = $1.00

        At A:

            40 gallons remain.

        A -> B requires:

            45 gallons.

        B is therefore outside the current range.

        The optimizer must fill at A.
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "2.00",
            ),
            self.create_station(
                "Station B",
                550,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=700,
            candidates=candidates,
        )

        self.assertGreaterEqual(
            len(plan.stops),
            1,
        )

        first_stop = plan.stops[0]

        self.assertEqual(
            first_stop.station.candidate.station.name,
            "Station A",
        )

        self.assertAlmostEqual(
            first_stop.gallons,
            10.0,
            places=6,
        )

    def test_waits_for_cheaper_reachable_station(self):
        """
        If a cheaper station can be reached with the current
        fuel, the optimizer should avoid buying expensive fuel
        at the current station.

        Route:

            Start ---- A -------- B -------- C ---- Destination
                     100        300        400       600

        Prices:

            A = $3.00
            B = $4.00
            C = $1.00

        Starting fuel = 50 gallons.

        Reach A with:

            40 gallons

        A -> C:

            300 miles
            30 gallons required

        Therefore C is reachable from A without refueling.

        The optimizer should skip A.

        At C:

            Fuel remaining = 10 gallons

        C -> destination:

            200 miles
            20 gallons required

        Therefore C fills the tank.

        Expected:

            A = no purchase
            B = no purchase
            C = 40 gallons
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "3.00",
            ),
            self.create_station(
                "Station B",
                300,
                "4.00",
            ),
            self.create_station(
                "Station C",
                400,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=600,
            candidates=candidates,
        )

        self.assertEqual(
            len(plan.stops),
            1,
        )

        stop = plan.stops[0]

        self.assertEqual(
            stop.station.candidate.station.name,
            "Station C",
        )

        self.assertEqual(
            stop.station.price,
            Decimal("1.00"),
        )

        self.assertAlmostEqual(
            stop.gallons,
            40.0,
            places=6,
        )

        self.assertEqual(
            stop.cost,
            Decimal("40.00"),
        )

        self.assertAlmostEqual(
            plan.total_gallons,
            40.0,
            places=6,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("40.00"),
        )

    # ==========================================================
    # MULTIPLE STATIONS
    # ==========================================================

    def test_multiple_price_stations(self):
        """
        A 500-mile route is exactly reachable with the
        initial 50-gallon tank.

        Station prices therefore have no effect.
        """

        candidates = [
            self.create_station(
                "A",
                100,
                "2.33",
            ),
            self.create_station(
                "B",
                200,
                "4.22",
            ),
            self.create_station(
                "C",
                300,
                "3.22",
            ),
            self.create_station(
                "D",
                400,
                "2.22",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=500,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

        self.assertEqual(
            plan.total_gallons,
            0.0,
        )

        self.assertEqual(
            plan.total_cost,
            Decimal("0"),
        )

    # ==========================================================
    # UNREACHABLE ROUTES
    # ==========================================================

    def test_unreachable_destination_raises_error(self):
        """
        The only station is 600 miles away.

        Starting range is 500 miles.

        Therefore the station cannot be reached and the
        destination cannot be reached.
        """

        candidates = [
            self.create_station(
                "Station A",
                600,
                "2.50",
            ),
        ]

        with self.assertRaises(ValueError):
            FuelOptimizer().optimize(
                route_distance_miles=700,
                candidates=candidates,
            )

    def test_unreachable_gap_between_stations_raises_error(self):
        """
        A station is reachable, but the next available station
        is outside the vehicle's full-tank range.

        Route:

            Start ---- A -------------------------- B
                     100                          650

        A is reachable.

        After filling A, maximum range is 500 miles.

        A -> B = 550 miles.

        Therefore B cannot be reached.

        The destination is also beyond the available range.
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "2.50",
            ),
            self.create_station(
                "Station B",
                650,
                "2.00",
            ),
        ]

        with self.assertRaises(ValueError):
            FuelOptimizer().optimize(
                route_distance_miles=800,
                candidates=candidates,
            )

    # ==========================================================
    # INVALID INPUT
    # ==========================================================

    def test_negative_route_distance_is_rejected(self):
        """
        Negative route distances are invalid.
        """

        with self.assertRaises(ValueError):
            FuelOptimizer().optimize(
                route_distance_miles=-10,
                candidates=[],
            )

    # ==========================================================
    # STATION FILTERING
    # ==========================================================

    def test_stations_at_start_are_ignored(self):
        """
        A station at position 0 is not useful because the
        vehicle starts before the station.
        """

        candidates = [
            self.create_station(
                "Start Station",
                0,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=400,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

    def test_stations_after_destination_are_ignored(self):
        """
        Stations after the destination cannot be used.
        """

        candidates = [
            self.create_station(
                "After Destination",
                600,
                "1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=500,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

    def test_negative_price_station_is_ignored(self):
        """
        Invalid negative fuel prices should not be used.
        """

        candidates = [
            self.create_station(
                "Invalid Station",
                100,
                "-1.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=400,
            candidates=candidates,
        )

        self.assertEqual(
            plan.stops,
            [],
        )

    # ==========================================================
    # RESULT ACCOUNTING
    # ==========================================================

    def test_total_gallons_and_cost_are_calculated_correctly(self):
        """
        Verify that the final FuelPlan correctly aggregates
        gallons purchased and total cost.
        """

        candidates = [
            self.create_station(
                "Station A",
                100,
                "2.00",
            ),
            self.create_station(
                "Station B",
                550,
                "3.00",
            ),
        ]

        plan = FuelOptimizer().optimize(
            route_distance_miles=1000,
            candidates=candidates,
        )

        self.assertGreater(
            len(plan.stops),
            0,
        )

        calculated_gallons = sum(
            stop.gallons
            for stop in plan.stops
        )

        calculated_cost = sum(
            (
                stop.cost
                for stop in plan.stops
            ),
            Decimal("0"),
        )

        self.assertAlmostEqual(
            plan.total_gallons,
            calculated_gallons,
            places=6,
        )

        self.assertEqual(
            plan.total_cost,
            calculated_cost,
        )