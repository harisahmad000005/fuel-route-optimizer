from dataclasses import dataclass

import requests
from django.conf import settings

NOMINATIM_URL = settings.NOMINATIM_URL


@dataclass(frozen=True)
class GeocodedLocation:
    """
    Represents a successfully geocoded US location.

    Attributes:
        location: The original city/state location string.
        latitude: Latitude returned by the geocoding service.
        longitude: Longitude returned by the geocoding service.
    """

    location: str
    latitude: float
    longitude: float


class GeocoderError(Exception):
    """
    Raised when a location cannot be successfully geocoded.

    This covers invalid input, network/request failures, invalid
    responses, missing results, and invalid coordinates.
    """


class NominatimGeocoder:
    """
    Geocode US city/state locations using the Nominatim API.

    The geocoder expects locations in the format:

        "City, ST"

    For example:

        "Chicago, IL"
        "Dallas, TX"

    The city and state are sent to Nominatim with the United States
    country restriction. The first matching result is converted into
    a `GeocodedLocation` containing the original location and its
    latitude/longitude.

    Responsibilities:
        - Validate and parse the city/state input.
        - Query the Nominatim geocoding API.
        - Restrict results to the United States.
        - Convert returned coordinates to floats.
        - Convert API/request errors into `GeocoderError`.

    Raises:
        GeocoderError:
            If the location is empty, incorrectly formatted, cannot
            be found, the API request fails, the response is invalid,
            or valid coordinates are not returned.
    """

    def geocode(self, location: str) -> GeocodedLocation:
        """
        Convert a city/state location into geographic coordinates.

        Args:
            location: A US location in the format "City, ST".

        Returns:
            A `GeocodedLocation` containing the normalized location
            and coordinates returned by Nominatim.

        Raises:
            GeocoderError: If the input is invalid, the API request
                fails, no location is found, or the response does
                not contain valid coordinates.
        """
        location = location.strip()

        if not location:
            raise GeocoderError("Location cannot be empty.")

        city, state = self._parse_location(location)

        params = {
            "city": city,
            "state": state,
            "country": "United States",
            "countrycodes": "us",
            "format": "jsonv2",
            "limit": 1,
        }

        headers = {
            "User-Agent": settings.GEOCODER_USER_AGENT,
        }

        try:
            response = requests.get(
                NOMINATIM_URL,
                params=params,
                headers=headers,
                timeout=30,
            )

            response.raise_for_status()

        except requests.RequestException as exc:
            raise GeocoderError(f"Unable to geocode location: {location}") from exc

        try:
            results = response.json()
        except ValueError as exc:
            raise GeocoderError(f"Invalid response from geocoder: {location}") from exc

        if not results:
            raise GeocoderError(f"Location not found: {location}")

        result = results[0]

        try:
            latitude = float(result["lat"])
            longitude = float(result["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeocoderError(
                f"Invalid coordinates returned for: {location}"
            ) from exc

        return GeocodedLocation(
            location=location,
            latitude=latitude,
            longitude=longitude,
        )

    @staticmethod
    def _parse_location(
        location: str,
    ) -> tuple[str, str]:
        """
        Parse and validate a location in "City, ST" format.

        Args:
            location: Raw city/state location string.

        Returns:
            A tuple containing `(city, state)`.

        Raises:
            GeocoderError: If the location does not contain exactly
                one city and one state component.
        """

        parts = [part.strip() for part in location.split(",")]

        if len(parts) != 2:
            raise GeocoderError("Location must be in the format 'City, ST'.")

        city, state = parts

        if not city or not state:
            raise GeocoderError("Location must be in the format 'City, ST'.")

        return city, state
