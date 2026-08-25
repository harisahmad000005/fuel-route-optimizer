"""
Seed fuel stations and cities from a CSV file.

Usage:
    python manage.py stations_data fuel_station_data/fuel_station.csv
    python manage.py stations_data fuel_station_data/fuel_station.csv --skip-geocode
    python manage.py stations_data fuel_station_data/fuel_station.csv --limit 50
    python manage.py stations_data fuel_station_data/fuel_station.csv

What it does:
- Creates or updates City records using (city, state).
- Geocodes each unique city once using Nominatim.
- Creates or updates FuelStation records using the unique OPIS ID.
- Uses city coordinates as a temporary fallback for stations.
- Leaves stations as PENDING for later address-level geocoding.
- Safe to re-run without creating duplicate stations.

Nominatim requests are limited to 1 request/second as required by
the public API usage policy.
"""

import csv
import logging
import time
from decimal import Decimal, InvalidOperation
from django.conf import settings
import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stations.models import City, FuelStation, GeocodeStatus

logger = logging.getLogger(__name__)


NOMINATIM_RATE_LIMIT_SECONDS = 1.0
GEOCODER_USER_AGENT = settings.GEOCODER_USER_AGENT
NOMINATIM_URL = settings.NOMINATIM_URL


def geocode_city(
    city_name: str,
    state: str,
) -> tuple[Decimal, Decimal] | None:
    """
    Geocode a city/state pair using Nominatim.

    Returns:
        (latitude, longitude) as Decimal values, or None if geocoding fails.
    """
    params = {
        "q": f"{city_name}, {state}, USA",
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
    }

    headers = {
        "User-Agent": GEOCODER_USER_AGENT,
    }

    try:
        response = requests.get(
            NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
        results = response.json()

    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Geocode failed for %s, %s: %s",
            city_name,
            state,
            exc,
        )
        return None

    if not results:
        logger.warning(
            "No geocoding result found for %s, %s",
            city_name,
            state,
        )
        return None

    try:
        latitude = Decimal(results[0]["lat"])
        longitude = Decimal(results[0]["lon"])

        return latitude, longitude

    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        logger.warning(
            "Invalid geocoding response for %s, %s: %s",
            city_name,
            state,
            exc,
        )
        return None


class Command(BaseCommand):
    help = (
        "Seed FuelStation and City data from a fuel-prices CSV "
        "with city-level geocoding."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            type=str,
            help="Path to the fuel prices CSV file",
        )

        parser.add_argument(
            "--skip-geocode",
            action="store_true",
            help="Load stations/cities without calling the geocoding API",
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Only process the first N rows",
        )

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        skip_geocode = options["skip_geocode"]
        limit = options["limit"]

        if limit is not None and limit <= 0:
            raise CommandError("--limit must be greater than 0")

        # ------------------------------------------------------------------
        # Pass 1: Read and validate CSV
        # ------------------------------------------------------------------
        try:
            rows = self._read_csv(csv_path, limit)
        except FileNotFoundError:
            raise CommandError(f"CSV file not found: {csv_path}")

        if not rows:
            self.stdout.write(self.style.WARNING("No valid rows found in CSV."))
            return

        self.stdout.write(f"Read {len(rows)} valid rows from {csv_path}")

        # ------------------------------------------------------------------
        # Pass 2: Create/get unique cities
        # ------------------------------------------------------------------
        unique_cities = {
            (
                row["city"].strip(),
                row["state"].strip().upper(),
            )
            for row in rows
        }

        self.stdout.write(f"Found {len(unique_cities)} unique city/state pairs")

        city_lookup: dict[tuple[str, str], City] = {}
        cities_needing_geocode: list[City] = []

        for city_name, state in sorted(unique_cities):
            city, _ = City.objects.get_or_create(
                name=city_name,
                state=state,
            )

            city_lookup[(city_name, state)] = city

            if city.geocode_status != GeocodeStatus.SUCCESS:
                cities_needing_geocode.append(city)

        self.stdout.write(
            f"{len(cities_needing_geocode)} cities need geocoding "
            f"({len(unique_cities) - len(cities_needing_geocode)} "
            f"already geocoded)"
        )

        # ------------------------------------------------------------------
        # Pass 3: Geocode unique cities
        # ------------------------------------------------------------------
        if skip_geocode:
            self.stdout.write(
                self.style.WARNING("Skipping geocoding (--skip-geocode set)")
            )
        else:
            for index, city in enumerate(
                cities_needing_geocode,
                start=1,
            ):
                coords = geocode_city(
                    city.name,
                    city.state,
                )

                if coords:
                    city.latitude, city.longitude = coords
                    city.geocode_status = GeocodeStatus.SUCCESS
                else:
                    city.geocode_status = GeocodeStatus.FAILED

                city.save(
                    update_fields=[
                        "latitude",
                        "longitude",
                        "geocode_status",
                    ]
                )

                self.stdout.write(
                    f"  [{index}/{len(cities_needing_geocode)}] " f"{city} -> {coords}"
                )

                # Respect Nominatim's rate limit, but don't sleep
                # unnecessarily after the final request.
                if index < len(cities_needing_geocode):
                    time.sleep(NOMINATIM_RATE_LIMIT_SECONDS)

        # ------------------------------------------------------------------
        # Pass 4: Upsert FuelStations
        # ------------------------------------------------------------------
        created_count = 0
        updated_count = 0

        with transaction.atomic():
            for row in rows:
                city_key = (
                    row["city"].strip(),
                    row["state"].strip().upper(),
                )

                city = city_lookup[city_key]

                defaults = {
                    "name": row["name"],
                    "address": row["address"],
                    "city": city,
                    "rack_id": row["rack_id"],
                    "price": row["price"],
                }

                station, created = FuelStation.objects.update_or_create(
                    opis_id=row["opis_id"],
                    defaults=defaults,
                )

                # Use city-level coordinates as an immediate fallback.
                # These are approximate coordinates, so the station remains
                # PENDING until exact station-level geocoding occurs.
                if (
                    station.latitude is None
                    and city.latitude is not None
                    and city.longitude is not None
                ):
                    station.latitude = city.latitude
                    station.longitude = city.longitude
                    station.geocode_status = GeocodeStatus.PENDING

                    station.save(
                        update_fields=[
                            "latitude",
                            "longitude",
                            "geocode_status",
                        ]
                    )

                if created:
                    created_count += 1
                else:
                    updated_count += 1

        # ------------------------------------------------------------------
        # Final result
        # ------------------------------------------------------------------
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. "
                f"{created_count} stations created, "
                f"{updated_count} updated."
            )
        )

    def _read_csv(
        self,
        csv_path: str,
        limit: int | None,
    ) -> list[dict]:
        """
        Parse the CSV into cleaned dictionaries.

        Malformed rows are skipped instead of crashing the entire import.
        """
        rows = []
        skipped = 0

        with open(
            csv_path,
            newline="",
            encoding="utf-8",
        ) as file:
            reader = csv.DictReader(file)

            for index, raw in enumerate(reader):
                if limit is not None and index >= limit:
                    break

                try:
                    opis_id = int(raw["OPIS Truckstop ID"].strip())

                    name = raw["Truckstop Name"].strip()
                    address = raw["Address"].strip()
                    city = raw["City"].strip()
                    state = raw["State"].strip().upper()

                    rack_id_raw = raw.get("Rack ID", "").strip()

                    rack_id = int(rack_id_raw) if rack_id_raw else None

                    price = Decimal(raw["Retail Price"].strip())

                    if not city:
                        raise ValueError("City cannot be empty")

                    if not state:
                        raise ValueError("State cannot be empty")

                    rows.append(
                        {
                            "opis_id": opis_id,
                            "name": name,
                            "address": address,
                            "city": city,
                            "state": state,
                            "rack_id": rack_id,
                            "price": price,
                        }
                    )

                except (
                    KeyError,
                    ValueError,
                    InvalidOperation,
                ) as exc:
                    skipped += 1

                    logger.warning(
                        "Skipping malformed row %s: %s",
                        index + 2,
                        exc,
                    )

        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped {skipped} malformed rows"))

        return rows
