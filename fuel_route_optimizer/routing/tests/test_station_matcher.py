from django.test import TestCase

from routing.services.routing import RoutingService
from routing.services.station_matcher import StationMatcher
from stations.models import FuelStation


class StationMatcherTest(TestCase):

    def test_match_stations_to_route(self):
        routing_service = RoutingService()

        route = routing_service.get_route(
            start_lat=34.0522,
            start_lon=-118.2437,
            end_lat=32.7767,
            end_lon=-96.7970,
        )

        midpoint = route.geometry[len(route.geometry) // 2]

        mid_lon, mid_lat = midpoint

        FuelStation.objects.create(
            opis_id=999001,
            name="Station On Route",
            address="123 Test Road",
            city="Test City",
            state="TX",
            latitude=mid_lat,
            longitude=mid_lon,
            geocode_status="success",
        )

        FuelStation.objects.create(
            opis_id=999002,
            name="Station Near Route",
            address="456 Test Road",
            city="Test City",
            state="TX",
            latitude=mid_lat + 0.01,
            longitude=mid_lon,
            geocode_status="success",
        )
        FuelStation.objects.create(
            opis_id=999003,
            name="Station Far From Route",
            address="789 Test Road",
            city="Test City",
            state="TX",
            latitude=mid_lat + 0.10,
            longitude=mid_lon,
            geocode_status="success",
        )

        matcher = StationMatcher()

        candidates = matcher.get_bounding_box_candidates(
            route.geometry
        )

        matched = matcher.match_stations_to_route(
            route.geometry,
            candidates,
        )

        print()

        for result in matched:
            print("Station:", result.station.name)
            print(
                "Distance from route:",
                result.distance_from_route_miles,
                "miles",
            )
            print(
                "Distance along route:",
                result.distance_along_route_miles,
                "miles",
            )
            print()

        self.assertEqual(len(matched), 2)
        station_names = {
            result.station.name
            for result in matched
        }

        self.assertIn(
            "Station On Route",
            station_names,
        )

        self.assertIn(
            "Station Near Route",
            station_names,
        )

        self.assertNotIn(
            "Station Far From Route",
            station_names,
        )

        on_route = next(
            result
            for result in matched
            if result.station.name == "Station On Route"
        )

        near_route = next(
            result
            for result in matched
            if result.station.name == "Station Near Route"
        )

        self.assertAlmostEqual(
            on_route.distance_from_route_miles,
            0,
            delta=0.01,
        )

        self.assertGreater(
            near_route.distance_from_route_miles,
            0,
        )

        self.assertLess(
            near_route.distance_from_route_miles,
            5,
        )

        self.assertGreater(
            on_route.distance_along_route_miles,
            0,
        )

        self.assertGreater(
            near_route.distance_along_route_miles,
            0,
        )

        self.assertLess(
            on_route.distance_along_route_miles,
            route.distance_miles,
        )

        self.assertLess(
            near_route.distance_along_route_miles,
            route.distance_miles,
        )