"""flight_cache.py — JSON checkpointing for FlightSource.search() calls."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date as Date, datetime, time as Time
from pathlib import Path
from typing import Iterable, List, Optional

from flight_sources.flight_source import FlightEndpoint, Flight, FlightSource


# Fixed namespace so a given trip's parameters always map to the same run
# directory, and a given (city, date) always maps to the same entry file -
# without putting any parameter text in the path. Deterministic uuid5 keeps
# resume working across runs.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "flight_tree_planner/flight_cache")

# Reserved entry key for the run metadata. It is hashed to a UUID like every
# other entry, so no path component reveals the parameters.
_META_KEY = "meta"

# Bumped when the on-disk schema changes incompatibly.
_SCHEMA_VERSION = 1


@dataclass
class _CachedSearch:
    """Everything persisted for one (departure city, departure date)."""

    flights: list[Flight] = field(default_factory=list)
    # (origin_airport_code, destination_airport_code) -> list of
    # "visited city path" keys that made this route a dead end.
    invalid: dict[tuple[str, str], list[str]] = field(default_factory=dict)


@dataclass
class _CacheMeta:
    """The trip parameters this cache directory belongs to, so an opaque
    UUID directory can still be identified."""

    origin: str
    start_date: str          # ISO date
    total_days: int
    nights_per_city: int
    earliest_departure: str  # "HHMM"
    latest_departure: str    # "HHMM"


# -- JSON (de)serialisation ------------------------------------------------
#
# The on-disk format is plain, indented JSON so the cache is human readable.
# Flight/FlightEndpoint stay pure DTOs; the mapping lives here.


def _endpoint_to_dict(endpoint: FlightEndpoint) -> dict:
    return {"airport_code": endpoint.airport_code, "time": endpoint.time.isoformat()}


def _endpoint_from_dict(raw: dict) -> FlightEndpoint:
    return FlightEndpoint(raw["airport_code"], datetime.fromisoformat(raw["time"]))


def _flight_to_dict(flight: Flight) -> dict:
    return {
        "origin": _endpoint_to_dict(flight.origin),
        "destination": _endpoint_to_dict(flight.destination),
        "price": flight.price,
        "arline": flight.arline,
        "arrival_city": flight.arrival_city,
        "arrival_country": flight.arrival_country,
    }


def _flight_from_dict(raw: dict) -> Flight:
    return Flight(
        _endpoint_from_dict(raw["origin"]),
        _endpoint_from_dict(raw["destination"]),
        raw["price"],
        raw.get("arline", ""),
        raw.get("arrival_city", ""),
        raw.get("arrival_country", ""),
    )


def _cached_search_to_dict(payload: _CachedSearch) -> dict:
    return {
        "version": _SCHEMA_VERSION,
        "flights": [_flight_to_dict(f) for f in payload.flights],
        "invalid": [
            {
                "origin": origin,
                "destination": destination,
                "visited": [path.split(",") for path in paths],
            }
            for (origin, destination), paths in payload.invalid.items()
        ],
    }


def _cached_search_from_dict(raw: dict) -> _CachedSearch:
    payload = _CachedSearch()
    for flight_raw in raw.get("flights", []):
        payload.flights.append(_flight_from_dict(flight_raw))
    for entry in raw.get("invalid", []):
        paths = [",".join(path) for path in entry.get("visited", [])]
        if paths:
            payload.invalid[(entry["origin"], entry["destination"])] = paths
    return payload


def _meta_to_dict(meta: _CacheMeta) -> dict:
    return {"version": _SCHEMA_VERSION, **asdict(meta)}


class FlightCache:
    """Persists raw search() results to JSON, one file per
    (departure city, departure date), so an interrupted scrape can resume
    without re-fetching combos it already has.

    No parameter text appears in any path component. Both the run directory
    and each entry file are deterministic UUIDv5 values derived from their
    parameters, so the same trip resolves to the same paths on every run.

    Layout:
        <root_dir>/<run_uuid>/<entry_uuid>.json
        <root_dir>/<run_uuid>/<meta_uuid>.json   (run metadata)

    The files are plain, indented JSON, so they are readable and editable
    by hand; unknown values are ignored on load.
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
        origin_norm = origin.strip().upper()
        param_key = "|".join([
            origin_norm,
            start_date.strftime("%Y-%m-%d"),
            str(total_days),
            str(nights_per_city),
            earliest_departure.strftime("%H%M"),
            latest_departure.strftime("%H%M"),
        ])
        self.run_uuid = uuid.uuid5(_NAMESPACE, param_key)
        self.run_dir = Path(root_dir) / str(self.run_uuid)
        self._meta = _CacheMeta(
            origin=origin_norm,
            start_date=start_date.strftime("%Y-%m-%d"),
            total_days=total_days,
            nights_per_city=nights_per_city,
            earliest_departure=earliest_departure.strftime("%H%M"),
            latest_departure=latest_departure.strftime("%H%M"),
        )

    # -- paths ------------------------------------------------------------

    def _entry_uuid(self, key: str) -> uuid.UUID:
        return uuid.uuid5(self.run_uuid, key)

    def _path(self, city_code: str, depart_date: Date) -> Path:
        key = f"{city_code}|{depart_date.strftime('%Y-%m-%d')}"
        return self.run_dir / f"{self._entry_uuid(key)}.json"

    def _meta_path(self) -> Path:
        return self.run_dir / f"{self._entry_uuid(_META_KEY)}.json"

    # -- JSON I/O ---------------------------------------------------------

    def _load_payload(self, path: Path) -> Optional[_CachedSearch]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return _cached_search_from_dict(raw)
        except Exception:
            # Missing, malformed, or stale-schema file -> treat as a cache miss.
            return None

    def _write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(tmp, path)  # atomic on the same filesystem

    def _ensure_meta(self) -> None:
        path = self._meta_path()
        if not path.exists():
            self._write(path, _meta_to_dict(self._meta))

    # -- public API -------------------------------------------------------

    def has(self, city_code: str, depart_date: Date) -> bool:
        return self._path(city_code, depart_date).exists()

    def load(self, city_code: str, depart_date: Date) -> Optional[List[Flight]]:
        payload = self._load_payload(self._path(city_code, depart_date))
        if payload is None:
            return None
        return payload.flights

    def save(self, city_code: str, depart_date: Date, flights: List[Flight]) -> None:
        path = self._path(city_code, depart_date)
        payload = self._load_payload(path) or _CachedSearch()
        payload.flights = list(flights)
        self._ensure_meta()
        self._write(path, _cached_search_to_dict(payload))

    def invalidate(self, origin, destination, depart_date, visited_cities) -> None:
        path = self._path(origin, depart_date)
        payload = self._load_payload(path)
        if payload is None:
            return
        # Mirror the previous behaviour: only routes that are actually in the
        # cached result can be marked invalid.
        if not any(
            f.origin.airport_code == origin and f.destination.airport_code == destination
            for f in payload.flights
        ):
            return
        keys = payload.invalid.setdefault((origin, destination), [])
        key = ",".join(visited_cities)
        if key not in keys:
            keys.append(key)
        self._write(path, _cached_search_to_dict(payload))

    def is_invalid(self, origin, destination, depart_date, visited_cities) -> bool:
        payload = self._load_payload(self._path(origin, depart_date))
        if payload is None:
            return False
        return ",".join(visited_cities) in payload.invalid.get((origin, destination), [])


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
