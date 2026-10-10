"""flight_cache.py — JSON checkpointing for FlightSource.search() calls."""

from __future__ import annotations

import json
from datetime import date as Date, datetime, time as Time
from pathlib import Path
from typing import Iterable, List, Optional, Set

from flight_sources.flight_source import FlightEndpoint, Flight, FlightSource


class FlightCache:
    """Persists raw search() results to JSON, one file per
    (departure city, departure date), so an interrupted scrape can resume
    without re-fetching combos it already has.

    Layout:
        <root_dir>/<origin>_<start_date>_<total_days>d_<nights_per_city>n/
            <depart_date>/
                <departure_city_code>.json
    """

    def __init__(
        self,
        origin: str,
        start_date: Date,
        total_days: int,
        nights_per_city: int,
        earliest_departure: Time,
        latest_departure: Time,
        root_dir: str | Path = "flight_cache",
    ) -> None:
        run_name = f"{origin}_{start_date.strftime("%Y-%m-%d")}_{total_days}d_{nights_per_city}n_{earliest_departure.strftime("%H%M")}_{latest_departure.strftime("%H%M")}"
        self.run_dir = Path(root_dir) / run_name

    def _path(self, city_code: str, depart_date: Date) -> Path:
        return self.run_dir / depart_date.strftime("%Y-%m-%d") / f"{city_code}.json"

    def has(self, city_code: str, depart_date: Date) -> bool:
        return self._path(city_code, depart_date).exists()

    def load(self, city_code: str, depart_date: Date) -> Optional[List[Flight]]:
        path = self._path(city_code, depart_date)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [_flight_from_dict(f) for f in raw]

    def save(self, city_code: str, depart_date: Date, flights: List[Flight]) -> None:
        path = self._path(city_code, depart_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = [_flight_to_dict(f) for f in flights]
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def invalidate(self, origin ,destination, depart_date, visited_cities) -> None :
        path = self._path(origin, depart_date)

        data = json.loads(path.read_text(encoding="utf-8"))

        for flight in data:
            if flight['origin']['airport_code'] == origin and flight['destination']['airport_code'] == destination:
                existing = flight.get('invalidFor', [])
                existing.append(','.join(visited_cities))
                flight["invalidFor"] = existing

        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def is_invalid(self, origin, destination, depart_date, visited_cities) -> bool:
        path = self._path(origin, depart_date)
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        for flight in data:
            if flight['origin']['airport_code'] == origin and flight['destination']['airport_code'] == destination:
                existing: list[str] = flight.get('invalidFor', [])
                return existing.__contains__(",".join(visited_cities));

        return False


def _city_to_dict(city: FlightEndpoint) -> dict:
        return {"airport_code": city.airport_code, "time": city.time.isoformat()}


def _city_from_dict(data: dict) -> FlightEndpoint:
        return FlightEndpoint(airport_code=data["airport_code"], time=datetime.fromisoformat(data["time"]))


def _flight_to_dict(flight: Flight) -> dict:
    return {
        "origin": _city_to_dict(flight.origin),
        "destination": _city_to_dict(flight.destination),
        "price": flight.price,
}


def _flight_from_dict(data: dict) -> Flight:
    return Flight(
            origin=_city_from_dict(data["origin"]),
            destination=_city_from_dict(data["destination"]),
            price=data["price"],
)



class CachingFlightSource(FlightSource):
    """Wraps a real FlightSource; caches search() by (origin, depart_date)."""

    def __init__(self, wrapped: FlightSource, cache: FlightCache) -> None:
        self._wrapped = wrapped
        self._cache = cache

    def search(
        self,
        origin: str,
        depart_date: Date,
        top_n: int,
        visited_cities: Iterable[str],
        price_threshold: float,
        excluded: Iterable[str],
    ) -> List[Flight]:
        cached = self._cache.load(origin, depart_date)
        if cached is not None:
            return cached

        flights = self._wrapped.search(
            origin=origin,
            depart_date=depart_date,
            top_n=top_n,
            visited_cities=visited_cities,
            price_threshold=price_threshold,
            excluded=excluded
        )
        self._cache.save(origin, depart_date, flights)
        return flights

    def get_home_flight(self, origin: str, destination: str, depart_date: Date, price_threshold: float, excluded_norm: Iterable[str]) -> Optional[Flight]:
        return self._wrapped.get_home_flight(origin, destination, depart_date, price_threshold, excluded_norm)

    def has_cache(self):
        return True

    def resolve_code(self, code):
        return self._wrapped.resolve_code(code)

    def resolve_city(self, city):
        return self._wrapped.resolve_city(city)