from .flight_source import FlightSource, City, Flight
import random

# --------------------------------------------------------------------------
# 1. Mock source - no network required. Great for testing the tree logic
#    and the visualization before wiring up a real API.
# --------------------------------------------------------------------------
class MockFlightSource(FlightSource):
    """Generates synthetic but plausible flight offers with a small built-in
    directory of cities, so name/country/exclusion features all work."""

    DIRECTORY = {
        "BER": ("Berlin", "Germany"),
        "CDG": ("Paris", "France"),
        "BCN": ("Barcelona", "Spain"),
        "FCO": ("Rome", "Italy"),
        "LIS": ("Lisbon", "Portugal"),
        "AMS": ("Amsterdam", "Netherlands"),
        "PRG": ("Prague", "Czechia"),
        "VIE": ("Vienna", "Austria"),
        "BUD": ("Budapest", "Hungary"),
        "ATH": ("Athens", "Greece"),
        "DUB": ("Dublin", "Ireland"),
        "CPH": ("Copenhagen", "Denmark"),
        "WAW": ("Warsaw", "Poland"),
        "OSL": ("Oslo", "Norway"),
        "MAD": ("Madrid", "Spain"),
        "MXP": ("Milan", "Italy"),
    }

    def __init__(self, seed: int | None = 42, price_range=(25, 220)):
        self.rng = random.Random(seed)
        self.price_range = price_range

    def resolve(self, code: str) -> City:
        code_u = code.upper()
        name, country = self.DIRECTORY.get(code_u, (code_u, "Unknown"))
        return City(code=code_u, name=name, country=country)

    def search(self, origin, depart_date, price_threshold, top_n=10, excluded=()):
        excluded_norm = self._normalize_excluded(excluded)
        candidates = [c for c in self.DIRECTORY if c != origin.upper()]
        self.rng.shuffle(candidates)

        flights = []
        for code in candidates:
            city = self.resolve(code)
            if self._is_excluded(city, excluded_norm):
                continue
            price = round(self.rng.uniform(*self.price_range), 2)
            if price <= price_threshold:
                flights.append(Flight(origin.upper(), city, depart_date, price))

        flights.sort(key=lambda f: f.price)
        return flights[:top_n]

    def get_price(self, origin, destination, depart_date):
        # Deterministic per (route, date) so repeated lookups are stable.
        key = f"{origin.upper()}-{destination.upper()}-{depart_date.isoformat()}"
        rng = random.Random(key)
        return round(rng.uniform(*self.price_range), 2)