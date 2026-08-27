"""
Import fuel stations and fuel prices from a CSV file.

Usage:
    python manage.py import_stations fuel_station_data/fuel_station.csv

Import without geocoding:
    python manage.py import_stations fuel_station_data/fuel_station.csv --skip-geocode

Import only first N rows:
    python manage.py import_stations fuel_station_data/fuel_station.csv --limit 50

Force re-geocoding:
    python manage.py import_stations fuel_station_data/fuel_station.csv --force-geocode
"""

import csv
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from stations.models import FuelPrice, FuelStation


class Command(BaseCommand):
    help = "Import fuel stations and fuel prices from a CSV file."

    # ==========================================================
    # OpenStreetMap Nominatim configuration
    # ==========================================================

    NOMINATIM_URL = settings.NOMINATIM_URL

    GEOCODER_TIMEOUT = 15

    # Nominatim's public service asks clients to keep requests
    # to approximately 1 request per second.
    GEOCODER_REQUEST_DELAY = 1.0

    USER_AGENT = "fuel-route-optimizer/1.0"

    # ==========================================================
    # Arguments
    # ==========================================================

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to the fuel station CSV file.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Import only the first N valid rows.",
        )

        parser.add_argument(
            "--skip-geocode",
            action="store_true",
            help="Import stations without geocoding them.",
        )

        parser.add_argument(
            "--force-geocode",
            action="store_true",
            help="Geocode stations even if coordinates already exist.",
        )

    # ==========================================================
    # Main
    # ==========================================================

    def handle(self, *args, **options):
        csv_file = Path(options["csv_file"])
        limit = options["limit"]
        skip_geocode = options["skip_geocode"]
        force_geocode = options["force_geocode"]

        if not csv_file.exists():
            self.stdout.write(
                self.style.ERROR(
                    f"CSV file does not exist: {csv_file}"
                )
            )
            return

        rows = self._read_csv(
            csv_file,
            limit,
        )

        if not rows:
            self.stdout.write(
                self.style.WARNING(
                    "No valid rows found in CSV."
                )
            )
            return

        self.stdout.write(
            f"Found {len(rows)} valid station records."
        )

        # ------------------------------------------------------
        # Counters
        # ------------------------------------------------------

        created = 0
        updated = 0

        prices_created = 0
        prices_unchanged = 0

        geocoded = 0
        already_geocoded = 0
        geocode_failed = 0

        observed_at = timezone.now()

        # ------------------------------------------------------
        # Process rows
        # ------------------------------------------------------

        for index, row in enumerate(
            rows,
            start=1,
        ):
            try:
                # --------------------------------------------------
                # Save station
                # --------------------------------------------------

                station, was_created = self._save_station(row)

                if was_created:
                    created += 1
                else:
                    updated += 1

                # --------------------------------------------------
                # Save price
                # --------------------------------------------------

                price_created = self._save_price(
                    station=station,
                    price=row["price"],
                    observed_at=observed_at,
                )

                if price_created:
                    prices_created += 1
                else:
                    prices_unchanged += 1

                # --------------------------------------------------
                # Skip geocoding
                # --------------------------------------------------

                if skip_geocode:
                    continue

                # --------------------------------------------------
                # Existing coordinates
                # --------------------------------------------------

                if (
                    station.latitude is not None
                    and station.longitude is not None
                    and not force_geocode
                ):
                    already_geocoded += 1

                    self.stdout.write(
                        f"[{index}/{len(rows)}] "
                        f"Already geocoded: "
                        f"{station.name} "
                        f"({station.city}, "
                        f"{station.state})"
                    )

                    continue

                # --------------------------------------------------
                # OpenStreetMap / Nominatim
                # --------------------------------------------------

                latitude, longitude = self._geocode_station(
                    station
                )

                if latitude is not None and longitude is not None:
                    station.latitude = latitude
                    station.longitude = longitude
                    station.geocode_status = "success"
                    station.geocoded_at = timezone.now()

                    station.save(
                        update_fields=[
                            "latitude",
                            "longitude",
                            "geocode_status",
                            "geocoded_at",
                        ]
                    )

                    geocoded += 1

                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[{index}/{len(rows)}] "
                            f"Geocoded: "
                            f"{station.name} "
                            f"({station.city}, "
                            f"{station.state}) "
                            f"→ "
                            f"{latitude}, "
                            f"{longitude}"
                        )
                    )

                else:
                    station.geocode_status = "failed"
                    station.geocoded_at = timezone.now()

                    station.save(
                        update_fields=[
                            "geocode_status",
                            "geocoded_at",
                        ]
                    )

                    geocode_failed += 1

                    self.stdout.write(
                        self.style.WARNING(
                            f"[{index}/{len(rows)}] "
                            f"No OSM match: "
                            f"{station.name} "
                            f"({station.city}, "
                            f"{station.state})"
                        )
                    )

            except Exception as exc:
                self.stdout.write(
                    self.style.ERROR(
                        f"[{index}/{len(rows)}] "
                        f"Failed to process row: {exc}"
                    )
                )

        # ======================================================
        # Summary
        # ======================================================

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                "Import complete."
            )
        )

        self.stdout.write(
            f"Stations created: {created}"
        )

        self.stdout.write(
            f"Stations updated: {updated}"
        )

        self.stdout.write(
            f"Prices created: {prices_created}"
        )

        self.stdout.write(
            f"Prices unchanged: {prices_unchanged}"
        )

        if not skip_geocode:
            self.stdout.write(
                f"Newly geocoded: {geocoded}"
            )

            self.stdout.write(
                f"Already had coordinates: "
                f"{already_geocoded}"
            )

            self.stdout.write(
                f"Geocoding failed: "
                f"{geocode_failed}"
            )

            self.stdout.write(
                "Geocoding service: "
                "OpenStreetMap Nominatim"
            )

    # ==========================================================
    # CSV
    # ==========================================================

    def _read_csv(
        self,
        csv_file,
        limit,
    ):
        rows = []

        with csv_file.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            reader = csv.DictReader(file)

            required_columns = {
                "OPIS Truckstop ID",
                "Truckstop Name",
                "Address",
                "City",
                "State",
                "Rack ID",
                "Retail Price",
            }

            missing_columns = (
                required_columns
                - set(reader.fieldnames or [])
            )

            if missing_columns:
                raise ValueError(
                    "CSV is missing required columns: "
                    + ", ".join(
                        sorted(missing_columns)
                    )
                )

            for row in reader:
                try:
                    cleaned = self._clean_row(row)

                    if cleaned is None:
                        continue

                    rows.append(cleaned)

                    if limit and len(rows) >= limit:
                        break

                except ValueError as exc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping invalid row: {exc}"
                        )
                    )

        return rows

    def _clean_row(
        self,
        row,
    ):
        opis_id = self._parse_int(
            row.get("OPIS Truckstop ID"),
            "OPIS Truckstop ID",
        )

        name = (
            row.get("Truckstop Name")
            or ""
        ).strip()

        address = (
            row.get("Address")
            or ""
        ).strip()

        city = (
            row.get("City")
            or ""
        ).strip()

        state = (
            row.get("State")
            or ""
        ).strip().upper()

        rack_id = self._parse_optional_int(
            row.get("Rack ID")
        )

        price = self._parse_decimal(
            row.get("Retail Price"),
            "Retail Price",
        )

        if not name:
            raise ValueError(
                "Truckstop Name is empty."
            )

        if not city:
            raise ValueError(
                "City is empty."
            )

        if not state:
            raise ValueError(
                "State is empty."
            )

        if len(state) != 2:
            raise ValueError(
                f"Invalid state code: {state}"
            )

        if price < 0:
            raise ValueError(
                f"Invalid negative price: {price}"
            )

        return {
            "opis_id": opis_id,
            "name": name,
            "address": address,
            "city": city,
            "state": state,
            "rack_id": rack_id,
            "price": price,
        }

    # ==========================================================
    # Station
    # ==========================================================

    @transaction.atomic
    def _save_station(
        self,
        row,
    ):
        station, created = (
            FuelStation.objects.update_or_create(
                opis_id=row["opis_id"],
                defaults={
                    "name": row["name"],
                    "address": row["address"],
                    "city": row["city"],
                    "state": row["state"],
                    "rack_id": row["rack_id"],
                },
            )
        )

        return station, created

    # ==========================================================
    # Fuel Price
    # ==========================================================

    def _save_price(
        self,
        station,
        price,
        observed_at,
    ):
        """
        Create a new price observation only when the
        current price differs from the latest price.
        """

        latest_price = (
            station.prices
            .order_by(
                "-observed_at",
                "-id",
            )
            .first()
        )

        if (
            latest_price is not None
            and latest_price.price == price
        ):
            return False

        FuelPrice.objects.create(
            station=station,
            price=price,
            observed_at=observed_at,
        )

        return True

    # ==========================================================
    # OpenStreetMap / Nominatim
    # ==========================================================

    def _geocode_station(
        self,
        station,
    ):
        """
        Geocode a fuel station using OpenStreetMap Nominatim.

        The CSV contains highway intersections and exits rather
        than conventional street addresses.

        Example:

            I-44, EXIT 283 & US-69
            Big Cabin, OK

        Multiple query formats are tried because Nominatim may
        recognize one representation but not another.
        """

        queries = self._build_geocoding_queries(
            station
        )

        if not queries:
            return None, None

        for query_index, query in enumerate(
            queries,
            start=1,
        ):
            self.stdout.write(
                f"Geocoding "
                f"{station.name} "
                f"({query_index}/{len(queries)}): "
                f"→ {query}"
            )

            result = self._query_nominatim(
                query
            )

            if result:
                return result

            # Respect Nominatim request rate.
            time.sleep(
                self.GEOCODER_REQUEST_DELAY
            )

        return None, None

    def _query_nominatim(
        self,
        query,
    ):
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "us",
            "addressdetails": 1,
        }

        try:
            response = requests.get(
                self.NOMINATIM_URL,
                params=params,
                timeout=self.GEOCODER_TIMEOUT,
                headers={
                    "User-Agent": self.USER_AGENT,
                },
            )

            response.raise_for_status()

            data = response.json()

        except requests.RequestException as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"Nominatim request failed: "
                    f"{exc}"
                )
            )
            return None

        except ValueError as exc:
            self.stdout.write(
                self.style.WARNING(
                    f"Invalid Nominatim response: "
                    f"{exc}"
                )
            )
            return None

        if not data:
            return None

        result = data[0]

        latitude = result.get("lat")
        longitude = result.get("lon")

        if latitude is None or longitude is None:
            return None

        display_name = result.get(
            "display_name",
            "",
        )

        self.stdout.write(
            f"OSM match: '{display_name}'"
        )

        return (
            Decimal(str(latitude)),
            Decimal(str(longitude)),
        )

    # ==========================================================
    # Build OSM queries
    # ==========================================================

    @classmethod
    def _build_geocoding_queries(
        cls,
        station,
    ):
        """
        Build several useful queries for Nominatim.

        The CSV frequently contains values such as:

            I-44, EXIT 283 & US-69

        We turn that into several possible queries:

            I-44, EXIT 283 & US-69, Big Cabin, OK, USA
            I-44 & US-69, Big Cabin, OK, USA
            I-44 EXIT 283, Big Cabin, OK, USA
        """

        address = (
            station.address or ""
        ).strip()

        city = station.city.strip()
        state = station.state.strip()

        queries = []

        # ------------------------------------------------------
        # 1. Original CSV location
        # ------------------------------------------------------

        if address:
            queries.append(
                f"{address}, "
                f"{city}, "
                f"{state}, USA"
            )

        # ------------------------------------------------------
        # 2. Normalized intersection
        # ------------------------------------------------------

        intersection = (
            cls._normalize_intersection(
                address
            )
        )

        if intersection:
            queries.append(
                f"{intersection}, "
                f"{city}, "
                f"{state}, USA"
            )

        # ------------------------------------------------------
        # 3. Highway + exit
        # ------------------------------------------------------

        highway_exit = (
            cls._extract_highway_exit(
                address
            )
        )

        if highway_exit:
            queries.append(
                f"{highway_exit}, "
                f"{city}, "
                f"{state}, USA"
            )

        # ------------------------------------------------------
        # 4. Station name + city/state
        #
        # Useful when OSM contains the actual truck stop.
        # ------------------------------------------------------

        name = (
            station.name or ""
        ).strip()

        if name:
            queries.append(
                f"{name}, "
                f"{city}, "
                f"{state}, USA"
            )

        # ------------------------------------------------------
        # Remove duplicate queries
        # ------------------------------------------------------

        return list(
            dict.fromkeys(
                query
                for query in queries
                if query.strip()
            )
        )

    @staticmethod
    def _normalize_intersection(
        address,
    ):
        if not address:
            return None

        value = address.upper().strip()

        # Remove EXIT information.
        value = re.sub(
            r"\bEXIT\s*[A-Z0-9\-]+\b",
            "",
            value,
        )

        # Convert commas to spaces.
        value = value.replace(
            ",",
            " ",
        )

        # Normalize whitespace.
        value = re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

        # Keep only the part containing an intersection.
        if "&" in value:
            parts = [
                part.strip()
                for part in value.split("&")
                if part.strip()
            ]

            if len(parts) >= 2:
                return " & ".join(parts)

        return None

    @staticmethod
    def _extract_highway_exit(
        address,
    ):
        if not address:
            return None

        match = re.search(
            r"((?:I-\d+|US-\d+|SR-\d+|"
            r"CR-\d+|STATE ROUTE\s+\d+)"
            r".*?EXIT\s*[A-Z0-9\-]+)",
            address.upper(),
        )

        if not match:
            return None

        value = match.group(1)

        value = re.sub(
            r"\s+",
            " ",
            value,
        ).strip()

        return value

    # ==========================================================
    # Parsing helpers
    # ==========================================================

    @staticmethod
    def _parse_int(
        value,
        field_name,
    ):
        try:
            return int(
                str(value).strip()
            )
        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                f"Invalid {field_name}: {value}"
            )

    @staticmethod
    def _parse_optional_int(
        value,
    ):
        value = str(
            value or ""
        ).strip()

        if not value:
            return None

        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_decimal(
        value,
        field_name,
    ):
        try:
            return Decimal(
                str(value).strip()
            )
        except (
            TypeError,
            ValueError,
            InvalidOperation,
        ):
            raise ValueError(
                f"Invalid {field_name}: {value}"
            )