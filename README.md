# Flight Tree Trip Planner

Builds a tree of multi-city trip itineraries starting from one departure
airport: at every city, it looks up the cheapest onward flights, keeps
only the ones under your price threshold, and recurses — until you run
out of days or a branch gets too expensive overall. The result is
rendered as an image (nodes = cities, edges = flight price).

## Files

- `flight_sources.py` — pluggable flight data providers (`MockFlightSource`,
  `KiwiTequilaSource`, `SkyscannerScraperSource`). Swap providers without
  touching anything else.
- `tree_builder.py` — the recursive search + pruning logic.
- `visualize.py` — renders the tree to a PNG.
- `main.py` — command-line entry point.

## Quick start (no API key, synthetic data)

```bash
pip install -r requirements.txt
python main.py \
  --origin BER \
  --start-date 2026-06-01 \
  --total-days 10 \
  --nights-per-city 2 \
  --price-threshold 120 \
  --max-total-cost 400 \
  --max-branching 4 \
  --source mock \
  --output trip_tree.png
```

This prints every complete itinerary (cheapest first) and saves
`trip_tree.png`.

## How the pruning works

- **`--price-threshold`**: a single flight above this price is discarded
  at the source — it never enters the tree.
- **`--max-total-cost`**: as soon as a branch's running total would exceed
  this, that whole twig (and everything below it) is cut.
- **`--total-days`** / **`--nights-per-city`**: a branch stops growing once
  there isn't enough of the day-budget left for another full stay. With
  `total-days=10` and `nights-per-city=2` you'll typically get 5-city
  chains (give or take, since the last stop may only use part of the
  final block of days).
- **`--max-branching`**: caps how many of the cheapest qualifying flights
  are kept at each city, so the tree can't explode combinatorially. Set
  it very high (e.g. `999`) if you genuinely want to keep *every* flight
  under the threshold — just be aware the tree (and the image) can get
  very large very fast: with branching factor *b* and *d* hops you get up
  to *b^d* leaves.

## Switching to real flight data

Mock data is only for testing the algorithm and the picture. For real
prices:

### Option A — Kiwi.com Tequila API (recommended)

Free tier, purpose-built for "flights from X to anywhere" queries.
Sign up at https://tequila.kiwi.com/portal/login for an API key, then:

```bash
python main.py --origin BER --start-date 2026-06-01 --total-days 10 \
  --price-threshold 120 --max-total-cost 400 \
  --source kiwi --kiwi-api-key YOUR_KEY_HERE
```

### Option B — Scraping Skyscanner directly

`SkyscannerScraperSource` in `flight_sources.py` is a best-effort
Playwright scraper. Before using it, please note:

- **Skyscanner's Terms of Use prohibit automated scraping** of their
  site. This is a ToS/contract issue, not a technical block — use at
  your own discretion and risk, and consider Option A instead for
  anything beyond a one-off personal experiment.
- Their result pages are JS-rendered and their markup changes
  periodically, so the CSS selectors in the scraper (`data-testid='...'`)
  are a starting point, not a guarantee — open the page in a browser,
  hit F12, and update the selectors if `search()` raises a "no result
  cards found" error.
- It runs `headless=False` by default so you can see and manually clear
  any cookie banner or human-verification prompt; it does not attempt to
  auto-solve CAPTCHAs or evade detection.
- Install Playwright first: `pip install playwright && playwright install chromium`.

```bash
python main.py --origin BER --start-date 2026-06-01 --total-days 10 \
  --price-threshold 120 --max-total-cost 400 --source skyscanner
```

## A note on the picture

The layout width grows with the number of leaves, so a wide/deep tree
(large `--max-branching` × many hops) produces a very wide image. For a
readable picture, keep `max-branching` modest (2-5) or reduce
`total-days`; for exhaustive analysis, use the printed itinerary ranking
in the console instead (it lists every surviving itinerary, cheapest
first, regardless of image size).
