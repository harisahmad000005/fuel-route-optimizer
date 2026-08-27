from django.db import models

from routing.mixins import TimeStampedModel


class RouteCache(TimeStampedModel):
    start_location = models.CharField(max_length=255)
    finish_location = models.CharField(max_length=255)

    start_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    start_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )

    finish_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    finish_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )

    route_distance_miles = models.FloatField()
    route_geometry = models.JSONField()

    fuel_total_purchased_gallons = models.FloatField()
    fuel_total_consumed_gallons = models.FloatField()
    fuel_remaining_gallons = models.FloatField()

    fuel_total_cost = models.DecimalField(
        max_digits=12,
        decimal_places=4,
    )

    fuel_stops = models.JSONField()

    price_fingerprint = models.CharField(
        max_length=64,
        db_index=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "start_location",
                    "finish_location",
                ],
                name="unique_route_cache",
            ),
        ]

    def __str__(self):
        return f"{self.start_location} → " f"{self.finish_location}"
