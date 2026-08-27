from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from routing.services.station_matcher import StationCandidate
from routing.services.station_prices import StationPriceService
from stations.models import FuelPrice, FuelStation


class StationPriceServiceTest(TestCase):

    def test_attach_latest_prices(self):
        now = timezone.now()

        station_a = FuelStation.objects.create(
            opis_id=999101,
            name="Station A",
            city="Dallas",
            state="TX",
            latitude=32.7767,
            longitude=-96.7970,
            geocode_status="success",
        )

        station_b = FuelStation.objects.create(
            opis_id=999102,
            name="Station B",
            city="Dallas",
            state="TX",
            latitude=32.7800,
            longitude=-96.7900,
            geocode_status="success",
        )

        station_c = FuelStation.objects.create(
            opis_id=999103,
            name="Station C",
            city="Dallas",
            state="TX",
            latitude=32.7850,
            longitude=-96.7850,
            geocode_status="success",
        )

        # Older price for Station A
        FuelPrice.objects.create(
            station=station_a,
            price=3.50,
            observed_at=now - timedelta(days=2),
        )

        # Latest price for Station A
        FuelPrice.objects.create(
            station=station_a,
            price=3.25,
            observed_at=now,
        )

        # Only price for Station B
        FuelPrice.objects.create(
            station=station_b,
            price=3.40,
            observed_at=now - timedelta(hours=2),
        )

        # Station C intentionally has no price.

        candidates = [
            StationCandidate(
                station=station_a,
                distance_from_route_miles=0.5,
                distance_along_route_miles=100.0,
            ),
            StationCandidate(
                station=station_b,
                distance_from_route_miles=1.2,
                distance_along_route_miles=200.0,
            ),
            StationCandidate(
                station=station_c,
                distance_from_route_miles=2.0,
                distance_along_route_miles=300.0,
            ),
        ]

        service = StationPriceService()

        results = service.attach_latest_prices(
            candidates
        )

        self.assertEqual(len(results), 2)

        station_names = {
            result.candidate.station.name
            for result in results
        }

        self.assertIn(
            "Station A",
            station_names,
        )

        self.assertIn(
            "Station B",
            station_names,
        )

        self.assertNotIn(
            "Station C",
            station_names,
        )

        station_a_result = next(
            result
            for result in results
            if result.candidate.station == station_a
        )

        self.assertEqual(
            station_a_result.price,
            3.25,
        )

        self.assertEqual(
            station_a_result.candidate.distance_along_route_miles,
            100.0,
        )