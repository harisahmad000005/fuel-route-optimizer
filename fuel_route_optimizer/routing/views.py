from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.services.geocoder import GeocoderError, NominatimGeocoder
from routing.services.route_optimizer import RouteOptimizer


class RouteOptimizeAPIView(APIView):

    def post(self, request):
        start_location = request.data.get("start_location")
        finish_location = request.data.get("finish_location")

        if not start_location or not finish_location:
            return Response(
                {"detail": ("start_location and finish_location " "are required.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        geocoder = NominatimGeocoder()

        try:
            start = geocoder.geocode(start_location)
            finish = geocoder.geocode(finish_location)
            result = RouteOptimizer().optimize(
                start_lat=start.latitude,
                start_lon=start.longitude,
                end_lat=finish.latitude,
                end_lon=finish.longitude,
            )

        except GeocoderError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            self._build_response(
                result=result,
                start=start,
                finish=finish,
            ),
            status=status.HTTP_200_OK,
        )

    def _build_response(self, result, start, finish):
        return {
            "start": {
                "location": start.location,
                "latitude": start.latitude,
                "longitude": start.longitude,
            },
            "finish": {
                "location": finish.location,
                "latitude": finish.latitude,
                "longitude": finish.longitude,
            },
            "route": {
                "distance_miles": result.route.distance_miles,
                "geometry": result.route.geometry,
            },
            "fuel": {
                "total_purchased_gallons": result.fuel_plan.total_purchased_gallons,
                "total_consumed_gallons": result.fuel_plan.total_consumed_gallons,
                "remaining_gallons": result.fuel_plan.remaining_gallons,
                "total_cost": str(result.fuel_plan.total_cost),
            },
            "stops": [
                {
                    "station": stop.station.candidate.station.name,
                    "address": stop.station.candidate.station.address,
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
                for stop in result.fuel_plan.stops
            ],
        }
