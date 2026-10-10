"""
Flight data source for the flight-tree trip planner.
All available flight sources are in flight_source.
This classe 

Every source implements the same interface:

    search(origin, depart_date, price_threshold, top_n, excluded)
        -> cheapest qualifying flights out of `origin`, for picking the
           NEXT city to visit.

    get_price(origin, destination, depart_date) -> float | None
        -> price of a specific point-to-point route (used for the
           mandatory "last flight home" leg, where we need one exact
           route rather than "anywhere").

    resolve(code) -> City
        -> best-effort lookup of a code's display name/country, used to
           label the root node.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date as Date
from typing import Iterable, List, Optional
from datetime import datetime
from airport_sources.airport_source import AirportSource
import requests

@dataclass
class City:
    name: str
    link: str
    lowest_price: int
    country: str


@dataclass(frozen=True)
class FlightEndpoint:
    airport_code: str
    time: datetime


@dataclass(frozen=True)
class Country:
    name: str
    link: str
    lowest_price: int

    def __str__(self):
        return f"{self.name}, {self.link}, {self.lowest_price}"

    def hasLink(self) -> bool:
        return self.link != None

@dataclass
class Flight:
    origin: FlightEndpoint          # IATA/city code of the departure point
    destination: FlightEndpoint
    price: float
    arline: str = ""
    arrival_city: str = ""
    arrival_country: str = ""


def _normalize_excluded(excluded: Iterable[str]) -> set:
    return {e.strip().upper() for e in excluded}


def _is_excluded(city: City, excluded_norm: set) -> bool:
    return city.code.upper() in excluded_norm or city.name.upper() in excluded_norm


class FlightSource(ABC):
    def __init__(self):
        self.airport_source: AirportSource

    @abstractmethod
    def search(
        self,
        origin: str,
        depart_date: Date,
        price_threshold: float,
        top_n: int = 10,
        excluded: Iterable[str] = (),
        visited_cities: list[str] = [],
    ) -> List[Flight]:
        """Cheapest qualifying flights from origin, for picking the next city."""
        raise NotImplementedError

    @abstractmethod
    def get_home_flight(self, origin: str, destination: str, depart_date: Date) -> Optional[Flight]:
        """Price of a specific origin->destination route, or None if unavailable."""
        raise NotImplementedError

    
    def resolve_code(self, code: str) -> str:
        return self.airport_source.get_city(code)

    def resolve_city(self, city: str) -> list[str]:
        return self.airport_source.get_iata_codes(city)

    def has_cache(self) -> bool:
        return False