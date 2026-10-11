# Flight Tree Trip Planner

Builds a tree of multi-city trip itineraries starting from one departure
airport: at every city it looks up the cheapest onward flights, keeps only
the ones under a derived price cap, and recurses until you run out of days
or a branch gets too expensive overall. Every itinerary in the tree ends
with a flight back to the origin.

Running it produces two things:

- a ranked list of the cheapest complete (round-trip) itineraries, printed
  to the console, and
- an interactive HTML page (nodes = cities, edges = flight price).

## Files

- `main.py` — Typer command-line entry point. Collects the arguments
  (prompting for anything you don't pass), builds the data source and its
  cache, runs the search, prints the ranking, and writes the HTML page.
- `tree_builder.py` — the recursive search + pruning logic. Public
  helpers: `build_trip_tree()`, `iter_itineraries()`,
  `iter_incomplete_branches()`, `count_nodes()`.
- `visualize.py` — serializes the tree and renders
  `trip_tree_template.html` into the output page via `draw_trip_tree()`.
- `trip_tree_template.html` — the D3.js template `visualize.py` fills in
  (the `__TITLE__`, `__TREE_DATA_JSON__` and `__COLOR_*__` tokens).
- `flight_sources/` — the pluggable data providers plus the on-disk cache:
  - `flight_source.py` — the `FlightSource` interface and the `City`,
    `Flight`, `FlightEndpoint`, and `Country` dataclasses.
  - `mock.py` — `MockFlightSource`, synthetic data with a small built-in
    city directory.
  - `kiwi.py` — `KiwiTequilaSource`, real flights via the Kiwi.com Tequila
    API.
  - `skyscanner.py` — `SkyscannerScraperSource`, a best-effort browser
    scraper.
  - `flight_cache.py` — `FlightCache` (JSON checkpoints) and
    `CachingFlightSource`.
- `airport_sources/` — the `AirportSource` interface and
  `CsvAirportSource`, which reads `airports.csv` to map IATA codes to city
  names and back.
- `flight_cache/` — generated JSON checkpoints (created at runtime and
  git-ignored).

## Install

```bash
pip install -r requirements.txt
```

The Skyscanner scraper additionally needs its browser binaries
(`patchright install chromium`).

## Usage

Anything you don't pass as a flag is asked for interactively. Pass
everything (or `--yes`) and it runs without prompts.

```bash
python main.py
```

A fully non-interactive run:

```bash
python main.py \
  --origin BER \
  --start-date 2026-06-01 \
  --total-days 10 \
  --nights-per-city 2 \
  --max-total-cost 400 \
  --max-branching 4 \
  --exclude CDG --exclude Rome \
  --source skyscanner \
  --output trip_tree.html \
  --yes
```

This prints every complete itinerary (cheapest first) and writes
`trip_tree.html`.

## Options

| Flag | Type | Default | Description |
| --- | --- | --- | --- |
| `--origin` | text | *(required)* | Departure airport/city code, e.g. `BER`. |
| `--start-date` | date | *(required)* | Start date (`DD.MM.YYYY` or `YYYY-MM-DD`). |
| `--total-days` | int | *(required)* | Total trip length in days. |
| `--max-total-cost` | float | *(required)* | Max cumulative cost before a branch is pruned. |
| `--nights-per-city` | int | `2` | Nights per city. |
| `--max-branching` | int | `5` | Max cheapest flights kept per node. |
| `--exclude`, `-x` | text | *(none)* | City names or IATA codes to never visit (repeatable). |
| `--earliest-departure` | time | `00:00` | Earliest departure time (`HH:MM`). |
| `--latest-departure` | time | `23:59` | Latest departure time (`HH:MM`). |
| `--source` | choice | `mock` | Flight data source: `mock`, `kiwi`, or `skyscanner`. |
| `--kiwi-api-key` | text | *(none)* | Kiwi API key; required when `--source kiwi`. |
| `--output` | text | `trip_tree.html` | Output path for the HTML page. |
| `--top-itineraries` | int | `10` | How many cheapest itineraries to print. |
| `--yes`, `-y` | flag | off | Never prompt; use the defaults. |

Notes:

- Only `origin`, `start_date`, `total_days`, and `max_total_cost` are
  required. If a required flag is missing in non-interactive mode, the CLI
  exits with an error such as `--origin is required`.
- There is no `--price-threshold` flag. The per-flight price cap is derived
  from `--max-total-cost` (see below).
- `--earliest-departure` must not be after `--latest-departure`.
- When you don't pass `--yes`, the CLI (a) prompts for each missing
  required value, then (b) shows one checklist where you can optionally
  change any of the optionals.

## How the search and pruning work

- The tree starts at `origin` on `start_date`, with no stay there; the
  first flights out are searched immediately for that date.
- At every city you stay exactly `nights_per_city` nights before the next
  flight. The day budget is split into roughly even chunks by
  `split_days()` so the stays cover `total_days`.
- A branch only continues to a **new** city if enough days remain for
  another full stay. No city is visited twice within one itinerary
  (origin included — it may only reappear as the final "flight home" leg).
- **Price cap (derived, not a flag):** a flight is only kept if its price
  is at or below a cap computed from the remaining budget. At the root the
  cap is `max_total_cost / (number of day-chunks + 1)`; after each hop it
  is recomputed from what's left of `max_total_cost` and the hops still to
  come. There is no user-facing `--price-threshold`.
- **`--max-total-cost`:** as soon as a branch's running total would exceed
  this, that whole twig (and everything below it) is cut.
- **`--total-days`** / **`--nights-per-city`:** a branch stops growing once
  there isn't enough of the day-budget left for another full stay. With
  `--total-days 10` and `--nights-per-city 2` you typically get 5-city
  chains (give or take).
- **`--max-branching`:** caps how many of the cheapest qualifying flights
  are kept at each city, so the tree can't explode combinatorially. Set it
  very high (e.g. `999`) if you genuinely want every flight under the cap —
  just be aware the tree (and the page) can get very large very fast: with
  branching factor *b* and *d* hops you get up to *b^d* leaves.
- **`--exclude`:** cities (by IATA code or name, case-insensitive) are
  never offered as a next stop. The origin itself cannot be excluded.
- **Dead ends:** once a branch can no longer continue, it must find a
  flight home to count as a finished itinerary — that is always the last
  leg. Branches that can't get home affordably are still kept in the tree
  and flagged `incomplete` so you can see why a path dead-ended.

## Output

`--output` writes a self-contained HTML page (D3.js loaded from a CDN,
everything else inlined). It is interactive rather than a static image:

- **Collapsible branches:** click a card with a chevron to fold/unfold its
  subtree.
- **Expand all** / **Collapse all** / **Fit view** buttons in the header,
  plus zoom/pan.
- **Horizontal, left-to-right layout:** the origin sits on the left and
  each flight moves one column right.

Node colors: navy = origin, blue = mid-trip city, gold = final flight
home. Dead ends are pruned out of the page entirely, so it only shows city
paths that actually reached a completed itinerary.

Besides the page, the console prints the tree's node count, the top
`--top-itineraries` complete itineraries with per-leg prices and totals,
and a note if any branches dead-ended.

## Flight data sources

All sources implement the `FlightSource` interface in
`flight_sources/flight_source.py`:

- `search(...)` — cheapest qualifying flights out of a city, used to pick
  the next city to visit.
- `get_home_flight(...)` — the price of a specific return route, used for
  the mandatory "flight home" leg.
- `resolve_code()` / `resolve_city()` — best-effort IATA code ↔ city-name
  lookups (backed by `airport_sources`, set via `set_airport_source()`).

`build_source()` in `main.py` wraps whichever source you choose in
`CachingFlightSource`, so repeated searches are served from the JSON cache.

- **`mock`** (`MockFlightSource`) — synthetic prices from a built-in city
  directory, no network required. Intended for testing the algorithm.
- **`kiwi`** (`KiwiTequilaSource`) — real flights via the Kiwi Tequila
  API; needs a free API key from
  <https://tequila.kiwi.com/portal/login>. Uses the `/v2/search`
  "fly_to=anywhere" endpoint.
- **`skyscanner`** (`SkyscannerScraperSource`) — a best-effort browser
  scraper. See the warning below.

## Switching to real flight data

For anything beyond a quick personal test, prefer a real flight-search API
over scraping. A good option that supports "cheapest flights from X to
anywhere" style queries:

- Kiwi.com "Tequila" API — <https://tequila.kiwi.com/portal/docs>
  (free tier, has an "anywhere" search mode).

### Skyscanner scraping caveats

`SkyscannerScraperSource` in `flight_sources/skyscanner.py` is a
best-effort scraper. Before using it, please note:

- **Skyscanner's Terms of Use prohibit automated scraping** of their site.
  This is a ToS/contract issue, not a technical block — use at your own
  discretion and risk, and prefer the Kiwi Tequila API for anything beyond
  a one-off personal experiment.
- Their result pages are JS-rendered and their markup changes
  periodically, so the selectors (`get_by_test_id(...)`, `PlaceCard_*`
  classes) are a starting point, not a guarantee — open the page in a
  browser, hit F12, and update the selectors if `search()` raises a
  "No result cards found" error.
- It runs `headless=False` by default so you can see and manually clear
  any cookie banner or human-verification prompt; it does not attempt to
  auto-solve CAPTCHAs. It loads a persistent Chromium profile from
  `./user-data` and a browser extension from `./nopecha`.
- It collects direct flights only by default (`direct_flights=True`) and
  filters to fares that include a cabin bag.

## The flight cache

`CachingFlightSource` persists each `search()` result to JSON so an
interrupted scrape can resume without re-fetching combinations it already
has. Layout:

```text
flight_cache/<origin>_<start_date>_<total_days>d_<nights_per_city>n_<earliest>_<latest>/
    <depart_date>/
        <departure_city_code>.json
```

Each file holds a list of flights (`origin`, `destination`, `price`).
Branches that turn out to be dead ends are recorded back into the cache as
an `invalidFor` list of visited-city paths, so they aren't re-explored.

## Known issues

These are current limitations of the code, not of the documentation:

- **Only `--source skyscanner` is runnable right now.**
  `MockFlightSource` and `KiwiTequilaSource` do not implement the current
  `FlightSource` interface (they define `get_price()` instead of
  `get_home_flight()`), so instantiating them raises
  `TypeError: Can't instantiate abstract class ... without an
  implementation for abstract method 'get_home_flight'`. They also
  construct `City`/`Flight` with the pre-refactor field shapes, which no
  longer match the dataclasses in `flight_source.py`.
- **`--output` help text** still says "Output PNG path" even though the
  output is an HTML page.
- **`FlightSource.has_cache`** is defined as a method but checked as a
  truthy attribute in `tree_builder.py`, so the cache code paths are
  always taken.
