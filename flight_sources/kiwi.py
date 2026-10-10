from .flight_source import FlightSource, Flight, City

# --------------------------------------------------------------------------
# 2. Kiwi.com Tequila API - real data, needs a free API key from
#    https://tequila.kiwi.com/portal/login
# --------------------------------------------------------------------------
class KiwiTequilaSource(FlightSource):
    BASE_URL = "https://api.tequila.kiwi.com/v2/search"

    def __init__(self, api_key: str, currency: str = "EUR"):
        self.api_key = api_key
        self.currency = currency

    def resolve(self, code: str) -> City:
        # Tequila's /search results carry a city name once the code shows up
        # as a destination; without a call to their separate /locations
        # endpoint we only have the code itself for a cold lookup (e.g. the
        # very first, root city). Good enough for the root label; every
        # other node gets the full name+country from search() results.
        return City(code=code.upper(), name=code.upper(), country="")

    def search(self, origin, depart_date, price_threshold, top_n=10, excluded=()):
        import requests  # local import so this dependency is optional

        excluded_norm = self._normalize_excluded(excluded)
        params = {
            "fly_from": origin,
            "fly_to": "anywhere",
            "date_from": depart_date.strftime("%d/%m/%Y"),
            "date_to": depart_date.strftime("%d/%m/%Y"),
            "curr": self.currency,
            "sort": "price",
            "limit": max(top_n * 4, 40),  # over-fetch, we filter/trim below
            "price_to": price_threshold,
            "one_for_city": 1,  # dedupe multiple offers to the same city
        }
        headers = {"apikey": self.api_key}
        resp = requests.get(self.BASE_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data", [])

        flights = []
        for item in data:
            code = item["flyTo"]
            name = item.get("cityTo", code)
            country = (item.get("countryTo") or {}).get("name", "")
            city = City(code=code.upper(), name=name, country=country)
            if self._is_excluded(city, excluded_norm):
                continue
            price = float(item["price"])
            if price <= price_threshold:
                flights.append(Flight(origin.upper(), city, depart_date, price))

        flights.sort(key=lambda f: f.price)
        return flights[:top_n]

    def get_price(self, origin, destination, depart_date):
        import requests

        params = {
            "fly_from": origin,
            "fly_to": destination,
            "date_from": depart_date.strftime("%d/%m/%Y"),
            "date_to": depart_date.strftime("%d/%m/%Y"),
            "curr": self.currency,
            "sort": "price",
            "limit": 1,
        }
        headers = {"apikey": self.api_key}
        resp = requests.get(self.BASE_URL, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return None
        return float(data[0]["price"])