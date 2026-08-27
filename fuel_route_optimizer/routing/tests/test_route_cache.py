from decimal import Decimal

from django.test import TestCase

from routing.models import RouteCache
from routing.services.fuel_optimizer import (
    FuelPlan,
    FuelStop,
)
from routing.services.route_cache import (
    RouteCacheService,
)
from routing.services.station_matcher import (
    StationCandidate,
)
from routing.services.station_prices import (
    PricedStationCandidate,
)
from stations.models import FuelStation


class RouteCacheServiceTest(TestCase):

    def setUp(self):
        self.service = RouteCacheService()

        self.station = FuelStation.objects.create(
            opis_id=100001,
            name="Test Fuel Station",
            address="123 Main Street",
            city="Chicago",
            state="IL",
            latitude=41.875561,
            longitude=-87.624421,
        )

        self.station_candidate = StationCandidate(
            station=self.station,
            distance_from_route_miles=0.5,
            distance_along_route_miles=100.0,
        )

        self.priced_station = PricedStationCandidate(
            candidate=self.station_candidate,
            price=Decimal("2.50"),
        )

    def create_fuel_plan(self):
        stop = FuelStop(
            station=self.priced_station,
            gallons=10.0,
            cost=Decimal("25.00"),
        )

        return FuelPlan(
            stops=[stop],
            total_gallons=10.0,
            total_cost=Decimal("25.00"),
        )

    def create_cache(self, fingerprint="abc123"):
        return RouteCache.objects.create(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
            start_latitude=Decimal("41.875561"),
            start_longitude=Decimal("-87.624421"),
            finish_latitude=Decimal("32.776272"),
            finish_longitude=Decimal("-96.796856"),
            route_distance_miles=920.5,
            route_geometry=[
                [-87.624421, 41.875561],
                [-96.796856, 32.776272],
            ],
            fuel_total_gallons=10.0,
            fuel_total_cost=Decimal("25.0000"),
            fuel_stops=[
                {
                    "station_id": self.station.id,
                    "station": "Test Fuel Station",
                    "city": "Chicago",
                    "state": "IL",
                    "price": "2.50",
                    "distance_along_route_miles": 100.0,
                    "distance_from_route_miles": 0.5,
                    "gallons": 10.0,
                    "cost": "25.00",
                }
            ],
            price_fingerprint=fingerprint,
        )

    # --------------------------------------------------
    # Cache lookup
    # --------------------------------------------------

    def test_get_route_cache_returns_none_when_cache_does_not_exist(self):
        cache = self.service.get_route_cache(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
        )

        self.assertIsNone(cache)

    def test_get_route_cache_returns_existing_cache(self):
        created = self.create_cache()

        cache = self.service.get_route_cache(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
        )

        self.assertIsNotNone(cache)
        self.assertEqual(cache.id, created.id)

    def test_different_route_does_not_return_cache(self):
        self.create_cache()

        cache = self.service.get_route_cache(
            start_location="New York, NY",
            finish_location="Dallas, TX",
        )

        self.assertIsNone(cache)

    # --------------------------------------------------
    # Price fingerprint
    # --------------------------------------------------

    def test_same_prices_generate_same_fingerprint(self):
        first = self.service.build_price_fingerprint(
            [self.priced_station]
        )

        second = self.service.build_price_fingerprint(
            [self.priced_station]
        )

        self.assertEqual(first, second)

    def test_changed_price_generates_different_fingerprint(self):
        first = self.service.build_price_fingerprint(
            [self.priced_station]
        )

        changed_station = PricedStationCandidate(
            candidate=self.station_candidate,
            price=Decimal("3.00"),
        )

        second = self.service.build_price_fingerprint(
            [changed_station]
        )

        self.assertNotEqual(first, second)

    def test_station_removal_changes_fingerprint(self):
        first = self.service.build_price_fingerprint(
            [self.priced_station]
        )

        second = self.service.build_price_fingerprint([])

        self.assertNotEqual(first, second)

    # --------------------------------------------------
    # Valid cache
    # --------------------------------------------------

    def test_matching_fingerprint_returns_cache(self):
        cache = self.create_cache(
            fingerprint="abc123"
        )

        result = self.service.get_valid_cache(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
            price_fingerprint="abc123",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.id, cache.id)

    def test_changed_fingerprint_invalidates_cache(self):
        cache = self.create_cache(
            fingerprint="old-fingerprint"
        )

        result = self.service.get_valid_cache(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
            price_fingerprint="new-fingerprint",
        )

        self.assertIsNone(result)

        self.assertFalse(
            RouteCache.objects.filter(
                id=cache.id
            ).exists()
        )

    # --------------------------------------------------
    # Save
    # --------------------------------------------------

    def test_save_creates_cache(self):
        fuel_plan = self.create_fuel_plan()

        cache = self.service.save(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
            start_latitude=41.875561,
            start_longitude=-87.624421,
            finish_latitude=32.776272,
            finish_longitude=-96.796856,
            route_distance_miles=920.5,
            route_geometry=[
                [-87.624421, 41.875561],
                [-96.796856, 32.776272],
            ],
            fuel_plan=fuel_plan,
            price_fingerprint="fingerprint-123",
        )

        self.assertIsNotNone(cache)

        self.assertEqual(
            cache.start_location,
            "Chicago, IL",
        )

        self.assertEqual(
            cache.finish_location,
            "Dallas, TX",
        )

        self.assertEqual(
            cache.route_distance_miles,
            920.5,
        )

        self.assertEqual(
            cache.fuel_total_gallons,
            10.0,
        )

        self.assertEqual(
            cache.fuel_total_cost,
            Decimal("25.0000"),
        )

        self.assertEqual(
            cache.price_fingerprint,
            "fingerprint-123",
        )

        self.assertEqual(
            len(cache.fuel_stops),
            1,
        )

    def test_save_updates_existing_route_instead_of_creating_duplicate(self):
        fuel_plan = self.create_fuel_plan()

        first = self.service.save(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
            start_latitude=41.875561,
            start_longitude=-87.624421,
            finish_latitude=32.776272,
            finish_longitude=-96.796856,
            route_distance_miles=920.5,
            route_geometry=[],
            fuel_plan=fuel_plan,
            price_fingerprint="old",
        )

        second = self.service.save(
            start_location="Chicago, IL",
            finish_location="Dallas, TX",
            start_latitude=41.875561,
            start_longitude=-87.624421,
            finish_latitude=32.776856,
            finish_longitude=-96.796856,
            route_distance_miles=921.5,
            route_geometry=[],
            fuel_plan=fuel_plan,
            price_fingerprint="new",
        )

        self.assertEqual(
            first.id,
            second.id,
        )

        self.assertEqual(
            RouteCache.objects.count(),
            1,
        )

        refreshed = RouteCache.objects.get(
            id=first.id
        )

        self.assertEqual(
            refreshed.route_distance_miles,
            921.5,
        )

        self.assertEqual(
            refreshed.price_fingerprint,
            "new",
        )

    # --------------------------------------------------
    # Fuel plan reconstruction
    # --------------------------------------------------

    def test_build_fuel_plan_from_cache(self):
        cache = self.create_cache()

        fuel_plan = (
            self.service.build_fuel_plan_from_cache(
                cache
            )
        )

        self.assertEqual(
            fuel_plan.total_gallons,
            10.0,
        )

        self.assertEqual(
            fuel_plan.total_cost,
            Decimal("25.0000"),
        )

        self.assertEqual(
            len(fuel_plan.stops),
            1,
        )

        stop = fuel_plan.stops[0]

        self.assertEqual(
            stop.station.candidate.station.id,
            self.station.id,
        )

        self.assertEqual(
            stop.station.candidate.station.name,
            "Test Fuel Station",
        )

        self.assertEqual(
            stop.station.price,
            Decimal("2.50"),
        )

        self.assertAlmostEqual(
            stop.gallons,
            10.0,
        )

        self.assertEqual(
            stop.cost,
            Decimal("25.00"),
        )