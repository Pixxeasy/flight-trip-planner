This package ships interchangeable flight data sources plus an on-disk
JSON cache,
so you can swap the data provider without touching the tree-building logic
at all. `flight_source.py` defines the shared `FlightSource` interface and
the `City`, `Flight`, `FlightEndpoint`, and `Country` dataclasses.

  1. MockFlightSource        - synthetic data, no network needed. Use this
                               to test/tune the algorithm and the graph
                               before you touch any real API.
  2. KiwiTequilaSource       - real flights via the Kiwi Tequila API
                               (needs a free API key).
  3. SkyscannerScraperSource - best-effort browser scraper. Off by
                               default; read the ToS warning in
                               `skyscanner.py` (and the project README)
                               first.

`flight_cache.py` adds `FlightCache` (readable JSON files under UUID-named
directories, one per departure city/date) and `CachingFlightSource`, which
wraps any source and serves repeated `search()` calls from disk. Each cache
entry carries a `fetched_at` timestamp and is treated as stale after one
hour.

For anything beyond a quick personal test, use a real flight-search API
instead. A good option that supports "cheapest flights from X to anywhere"
style queries:

  * Kiwi.com "Tequila" API      https://tequila.kiwi.com/portal/docs
                                 (free tier, has a /search "anywhere"
                                 mode - fly_to=anywhere)

Current status: only SkyscannerScraperSource implements the full current
`FlightSource` interface and can be instantiated. MockFlightSource and
KiwiTequilaSource still define `get_price()` instead of
`get_home_flight()` and use the pre-refactor `City`/`Flight` shapes, so
they raise `TypeError` on instantiation until they are updated. See the
"Known issues" section of the project README.
