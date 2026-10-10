This module ships three interchangeable sources so you can swap them out
without touching the tree-building logic at all:

  1. MockFlightSource        - synthetic data, no network needed. Use this
                                to test/tune the algorithm and the graph
                                before you touch any real API.
  2. KiwiTequilaSource       - real flights via the Kiwi Tequila API
                                (needs a free API key).
  3. SkyscannerScraperSource - best-effort Playwright scraper. Off by
                                default, read the warning above first.

For anything beyond a quick personal test, use a real flight-search API
instead. Good options that support "cheapest flights from X to anywhere"
style queries:

  * Kiwi.com "Tequila" API      https://tequila.kiwi.com/portal/docs
                                 (free tier, has a /search "anywhere"
                                 mode - fly_to=anywhere)
  * Amadeus Self-Service API    https://developers.amadeus.com/
                                 (free tier, "Flight Cheapest Date Search")