"""
IMPORTANT - read before using SkyscannerScraperSource
-------------------------------------------------------
Skyscanner's Terms of Use prohibit automated scraping / bulk querying of
their site, and they run active bot-detection (rate limiting, JS
challenges, IP blocking). A scraper built against their public HTML is:

  * fragile      - it breaks whenever they change markup/JS bundles
  * against ToS  - fine for a one-off personal experiment, risky for
                   anything more sustained
  * likely to    - get your IP rate-limited or blocked outright
    get blocked
"""

# --------------------------------------------------------------------------
# 3. Skyscanner scraper - best effort, off by default. Requires:
#       pip install playwright
#       playwright install chromium
#    Read the module docstring warning above before relying on this.
# --------------------------------------------------------------------------
from .flight_source import FlightSource, Flight, City, Country, FlightEndpoint
from .guarded_page import GuardedPage
from airport_sources.csv_souce import CsvAirportSource
from typing import List, Iterable
from patchright.sync_api import expect, Page, TimeoutError as PlaywrightTimeoutError, Locator
from datetime import datetime, timedelta, date, time as Time

import re
import time
from babel.numbers import parse_decimal
from pathlib import Path

class SkyscannerScraperSource(FlightSource):
    """
    Best-effort scraper for Skyscanner's "Everywhere" search results.

    Because Skyscanner's cards show a city NAME rather than an IATA code,
    this tries to recover a stable code from the result card's link
    (Skyscanner route URLs embed a short place slug, e.g.
    ".../flights/ber/pari/..."). That slug is what gets fed back in as
    `origin` on the next hop, so multi-hop chains stay consistent. This is
    inherently fragile - if Skyscanner changes its URL scheme or markup,
    fix the extraction logic below (inspect with devtools, F12).
    """
    direct_flights_only: bool
    earliest_departure: int
    latest_departure: int

    BASE_URL = "https://www.skyscanner.de/transport/flights"

    def __init__(self, earliest_departure: Time, latest_departure: Time ,headless: bool = False, request_delay_s: float = 3.0, direct_flights: bool = True):
        self.headless = headless
        self.request_delay_s = request_delay_s
        self.direct_flights_only = direct_flights
        self.hand_luggage = True
        self.earliest_departure = earliest_departure.hour * 60 + earliest_departure.minute
        self.latest_departure = latest_departure.hour * 60 + latest_departure.minute

    def set_airport_source(self, filepath: str):
        self.airport_source = CsvAirportSource(filepath)

    def _recover_from_captcha(self, page: GuardedPage, url: str) -> bool:
        """If a captcha page is present, wait it out and return to `url`.

        Returns True when the page is usable again, False when there was
        no captcha to begin with or it could not be cleared.
        """
        if not page.is_captcha():
            return False
        page.settle(intended_url=url)
        return not page.is_captcha()


    def build_url(self,origin, depart_date):
        return (  
            f"{self.BASE_URL}/{origin.lower()}/"
            f"{depart_date.strftime('%y%m%d')}/"
            f"?adultsV2=1&cabinclass=economy&childrenV2=&rtn=0&outboundaltsenabled=false&inboundaltsenabled=false"
            f"{'&fare-attributes=cabin-bag' if self.hand_luggage else ''}"
            f"{'&stops=direct' if self.direct_flights_only else ''}")

    def get_browser(self,p):
        return p.chromium.launch_persistent_context(
                 headless=self.headless,
                 user_data_dir="./user-data",
                 no_viewport=True,
                 channel="chromium",
                 args=[
                    f"--disable-extensions-except={str(Path("./nopecha").resolve())}",
                    f"--load-extension={str(Path("./nopecha").resolve())}",
                ],
            )

    def search(self, origin, price_threshold, depart_date: date, top_n=10, visited_cities = [], excluded: Iterable[str] = ()):
        import time
        from patchright.sync_api import sync_playwright, expect

        url = self.build_url(origin, depart_date)

        flights: List[Flight] = []
        with sync_playwright() as p:
            browser = self.get_browser(p)

            page: GuardedPage = GuardedPage(browser.new_page())
            page.goto("https://www.skyscanner.de", timeout=60_000)
            time.sleep(3)
            page.goto(url)

            time.sleep(1)
            countries: list[Country] = []
            try:
                page.get_by_test_id("place-card").first.wait_for(state="visible", timeout=10_000)
            except Exception:
                if not self._recover_from_captcha(page, url):
                    return []
                try:
                    page.get_by_test_id("place-card").first.wait_for(state="visible", timeout=10_000)
                except Exception:
                    return []

            country_cards = page.get_by_test_id("place-card").all()

            for country_card in country_cards:
                try:
                    description_el = country_card.get_by_test_id("description-container")

                    country_el = description_el.locator("[class^='PlaceCard_nameContent']").locator("h2").locator("span")
                    price_el = description_el.locator("span").locator("div").locator("div").locator("span")

                    if not country_el or not price_el:
                        continue

                    country_name = country_el.inner_text().strip()

                    # Price gets returned as "<price> <currency>" e.g. "20 €", we only need the first part
                    #price_value = price_el.inner_text()
                    price_value = price_el.inner_text().split(" ")[0]


                    href = country_card.get_attribute("href")
                    countries.append(Country(country_name,href, int(price_value)))
                    print(Country(country_name, href,int(price_value) ))

                except Exception:
                    continue  # skip cards we can't parse rather than crash the whole run

            flights: list[Flight] = []
            for country in countries:
                if country.hasLink() and country.lowest_price <= price_threshold:
                    flights.extend(self.search_country(country, page, browser, depart_date, visited_cities, price_threshold, excluded))

            browser.close()

        time.sleep(self.request_delay_s)  # be polite between successive calls
        flights.sort(key=lambda f: f.price)
        return flights[:top_n]

    def search_country(self, country: Country, page: GuardedPage, browser, depart_date: date, visited_cities, price_threshold: float, excluded_cities) -> list[Flight]: 
        page.goto(country.link, timeout=60_000)

        try:
                page.get_by_test_id("place-card").first.wait_for(state="visible", timeout=10_000)
        except Exception:
                if not self._recover_from_captcha(page, country.link):
                    browser.close()
                    raise RuntimeError(
                        "No result cards found - Skyscanner's markup or "
                        "loading behaviour has likely changed. Open the URL "
                        "manually and inspect the DOM with devtools to find "
                        "the current selectors."
                    )
                try:
                    page.get_by_test_id("place-card").first.wait_for(state="visible", timeout=10_000)
                except Exception:
                    browser.close()
                    raise RuntimeError(
                        "No result cards found - Skyscanner's markup or "
                        "loading behaviour has likely changed. Open the URL "
                        "manually and inspect the DOM with devtools to find "
                        "the current selectors."
                    )

        cities: list[City] = []

        city_cards = page.get_by_test_id("place-card").all()
        for city_card in city_cards:
            try:
                description_el = city_card.get_by_test_id("description-container")
                city_el = description_el.locator("[class^='PlaceCard_nameContent']").locator("h2").locator("span")
                price_el = description_el.locator("a").filter(has_text=re.compile(r"^\d+[\d.,]*\s?€$")).first
                link_el = description_el.get_by_test_id("flights-link")
                if not city_el or not price_el:
                        continue

                city_name = city_el.inner_text().strip()

                # Price gets returned as "<price> <currency>" e.g. "20 €", we only need the first part
                price_value = price_el.inner_text().split(" ")[0]

                href = link_el.get_attribute("href") if link_el else None
                if href != None and int(price_value) < price_threshold:
                    cities.append(City(city_name,href, int(price_value),country.name))

            except Exception as e:
                print(e)
                continue  # skip cards we can't parse rather than crash the whole run
        flights: list[Flight] = []
        for city in cities:
            try:
                if self.is_already_visited(city.name, visited_cities):
                    continue
            except Exception as e:
                print(e)
                continue
            flights.extend(self.search_flights(city, page, browser, depart_date, price_threshold, excluded_cities))
                    
        return flights

    def is_already_visited(self, city_name: str, visited_cities: list[str]) -> bool:
        iata_codes = self.resolve_city(city_name)
        return bool(set(iata_codes) & set(visited_cities))

    def search_flights(self, city: City, page: GuardedPage, browser, depart_date: date, price_threshold: float, excluded_cities: Iterable[str]) -> list[Flight]:
        link = city.link + f"&departure-times={self.earliest_departure}-{self.latest_departure}"

        page.goto(link, timeout=60_000)

        flights_found_selector = "[id='resultsContainer']"
        flights: list[Flight] = []
        try:
            expect(page.locator(flights_found_selector).locator("span").first).to_contain_text("Ergebnis",timeout=120_000)
        except Exception as e:
                if not self._recover_from_captcha(page, link):
                    print(e)
                    browser.close()
                    raise RuntimeError(
                        "No result cards found - Skyscanner's markup or "
                        "loading behaviour has likely changed. Open the URL "
                        "manually and inspect the DOM with devtools to find "
                        "the current selectors."
                    )
                try:
                    expect(page.locator(flights_found_selector).locator("span").first).to_contain_text("Ergebnis",timeout=120_000)
                except Exception as e:
                    print(e)
                    browser.close()
                    raise RuntimeError(
                        "No result cards found - Skyscanner's markup or "
                        "loading behaviour has likely changed. Open the URL "
                        "manually and inspect the DOM with devtools to find "
                        "the current selectors."
                    )
        time.sleep(1)
        if self.has_no_results(page):
            return flights
        try:
            if self.get_cheapest_flight(page) > price_threshold:
                return flights
        except:
            return flights
        page.get_by_test_id("QuickSort").locator("label").nth(1).click()
        time.sleep(2)

        flight_cards = page.get_by_role("listitem").filter(has=page.get_by_test_id("ticket")).all()
        for flight_card in flight_cards:
            if self.is_sponsored(flight_card) or self.is_rail_banner(flight_card):
                continue

            price = flight_card.get_by_test_id("price-main-container").inner_text();
            try:
                airline =  flight_card.get_by_test_id("TicketBodyCarrierHeader").inner_text(timeout=3_000);
            except PlaywrightTimeoutError:
                airline =  flight_card.locator("[class^='LegLogo_legImage']").locator("img").get_attribute("alt")
            

            depart_el = flight_card.get_by_test_id("route-partial-time").first
            depart_code = flight_card.get_by_test_id("leg-info-row").locator("[data-backpack-ds-component='Flex']").first.locator("span").last.inner_text();
            depart_time = depart_el.inner_text();

            if depart_code in excluded_cities:
                continue

            arrival_el = flight_card.get_by_test_id("route-partial-time").last
            arrival_code = flight_card.get_by_test_id("leg-info-row").locator("[data-backpack-ds-component='Flex']").last.locator("span").last.inner_text();
            arrival_time = arrival_el.inner_text();

            dep = self.create_flight_endpoint(depart_code,depart_time, depart_date)
            arrival = self.create_flight_endpoint(arrival_code,arrival_time, depart_date)

            price_value = price.split(" ")[0]

            parsed_price = parse_decimal(price_value, locale="de")

            if (int(parsed_price) > price_threshold):
                break
            f = Flight(dep,arrival,float(parsed_price),airline, city.name, city.country)
            flights.append(f)
            return flights
        return flights

    def is_sponsored(self, ticket: Locator) -> bool:
        return ticket.get_by_test_id("adInfoBtn").count() > 0;

    def is_rail_banner(self, ticket) -> bool:
        return ticket.locator("[class^='RailBanner']").count() > 0

    def has_no_results(self, page: Page) -> bool:
        flights_found_selector = "[id='resultsContainer']"
        return page.locator(flights_found_selector).locator("span").first.inner_text().startswith("0 von")

    def get_cheapest_flight(self, page: Page) -> int:
       return int(page.get_by_test_id("QuickSort").locator("[data-part='item']").nth(1).get_by_test_id("price-main-container").inner_text().split(" ")[0])

    def create_flight_endpoint(self, code: str, time_value: str, depart_date: date) -> FlightEndpoint:
        add_day = time_value.__contains__("+1")
        if add_day:
            time_value = time_value.replace("\n+1", "")

        time_obj: Time = datetime.strptime(time_value,'%H:%M').time()
        date_obj: datetime = datetime.combine(depart_date,time_obj)
        if add_day:
            date_obj = date_obj + timedelta(days=1)

        return FlightEndpoint(code, date_obj)

    def get_home_flight(self, origin, destination, depart_date, price_threshold, excluded_cities) -> Flight:
        import time
        from patchright.sync_api import sync_playwright

        url = (
            f"https://www.skyscanner.de/transport/flights/{origin.lower()}/"
            f"{destination.lower()}/{depart_date.strftime('%y%m%d')}/"
            f"?adultsV2=1&cabinclass=economy&childrenV2=&rtn=0&outboundaltsenabled=false&inboundaltsenabled=false"
            f"{'&fare-attributes=cabin-bag' if self.hand_luggage else ''}"
            f"{'&preferdirects=true' if self.direct_flights_only else ''}" 
        )
        with sync_playwright() as p:

            browser = self.get_browser(p)

            page: GuardedPage = GuardedPage(browser.new_page())
            page.goto("https://www.skyscanner.de", timeout=60_000)
            time.sleep(3)

            city_name = self.resolve_code(origin)
            fl_list: list[Flight] = self.search_flights(City(city_name,url,0,""),page,browser,depart_date, price_threshold=price_threshold, excluded_cities=excluded_cities)
            flight = None
            if len(fl_list) > 0:
                flight = fl_list[0]

            browser.close()
        time.sleep(self.request_delay_s)
        return flight