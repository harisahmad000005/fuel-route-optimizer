from dataclasses import dataclass
from decimal import Decimal

from django.db.models import OuterRef, Subquery

from routing.services.station_matcher import StationCandidate
from stations.models import FuelPrice, FuelStation


@dataclass
class PricedStationCandidate:
    """
    Represents a route-matched fuel station together with
    its latest known fuel price.

    Attributes:
        candidate:
            Station information produced by StationMatcher,
            including its distance from and position along
            the route.

        price:
            The station's latest available fuel price per gallon.
    """

    candidate: StationCandidate
    price: Decimal


class StationPriceService:
    """
    Enriches route station candidates with their latest fuel prices.

    StationMatcher determines which stations are useful for the
    route, but it does not determine their current fuel prices.

    This service connects the matched stations with FuelPrice records
    and retrieves the most recent price for each station.

    Stations without a recorded fuel price are excluded because the
    fuel optimizer cannot calculate a meaningful fueling decision
    without a price.
    """

    def attach_latest_prices(
        self,
        candidates: list[StationCandidate],
    ) -> list[PricedStationCandidate]:
        """
        Attach the latest fuel price to every matched station.

        The method uses a Django Subquery to retrieve the most recent
        FuelPrice for each station. Prices are ordered by observation
        time, with the record ID used as a deterministic tie-breaker
        when two prices have the same timestamp.

        The database query is performed for all candidate stations
        rather than querying the price table separately for each
        station. This avoids an N+1 query pattern.

        Candidates without a known fuel price are removed because
        they cannot be evaluated by the fuel optimizer.

        Args:
            candidates:
                Stations that have already been matched to the route.

        Returns:
            A list of PricedStationCandidate objects containing
            route information and the latest available fuel price.

        Returns an empty list when no candidates are provided.
        """

        if not candidates:
            return []

        # Extract the database IDs of the stations that survived
        # route matching.
        station_ids = [
            candidate.station.id
            for candidate in candidates
        ]

        # Retrieve the latest price for each station.
        #
        # OuterRef("pk") refers to the FuelStation currently being
        # evaluated by the outer query.
        #
        # Ordering by observed_at and id ensures that the newest
        # price is selected deterministically.
        latest_price_subquery = (
            FuelPrice.objects
            .filter(
                station_id=OuterRef("pk"),
            )
            .order_by("-observed_at", "-id")
            .values("price")[:1]
        )

        # Fetch all relevant stations and annotate each one with
        # its latest fuel price.
        #
        # This keeps the operation database-oriented instead of
        # performing one price query per station.
        stations = (
            FuelStation.objects
            .filter(id__in=station_ids)
            .annotate(
                latest_fuel_price=Subquery(
                    latest_price_subquery
                ),
            )
        )

        # Build a lookup so the original StationCandidate objects
        # can be enriched without repeatedly searching the query
        # result.
        price_by_station_id = {
            station.id: station.latest_fuel_price
            for station in stations
            if station.latest_fuel_price is not None
        }

        results = []

        for candidate in candidates:

            price = price_by_station_id.get(
                candidate.station.id
            )

            # A station without a price cannot participate in
            # cost optimization, so it is ignored.
            if price is None:
                continue

            results.append(
                PricedStationCandidate(
                    candidate=candidate,
                    price=price,
                )
            )

        return results