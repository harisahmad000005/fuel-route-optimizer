from django.test import TestCase

from routing.services.routing import RoutingService


class RoutingServiceTest(TestCase):

    def test_get_route(self):
        service = RoutingService()

        result = service.get_route(
            start_lat=34.0522,
            start_lon=-118.2437,
            end_lat=32.7767,
            end_lon=-96.7970,
        )

        print("Distance:", result.distance_miles)
        print("Number of route points:", len(result.geometry))
        print("First point:", result.geometry[0])
        print("Last point:", result.geometry[-1])

        self.assertGreater(result.distance_miles, 0)
        self.assertGreater(len(result.geometry), 0)