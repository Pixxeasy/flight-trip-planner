"""
Core trip-tree search.

Semantics
---------
- You start at `origin` on `start_date` with no stay there - the first
  flights out are searched immediately on that date.
- Every city you fly to, you stay exactly `nights_per_city` nights before
  the next flight.
- No city is ever visited twice within one itinerary (the origin included
  - it may only reappear as the final "flight home" leg).
- A branch continues to a NEW city only if enough days remain in the
  total budget for another full `nights_per_city`-night stay afterwards.
- Once a branch can no longer continue on to a new city (either because
  the days are used up, or because no qualifying/unvisited/non-excluded
  destination exists), it must find a flight back to `origin` to count as
  a finished itinerary - this is always the last leg. If no such flight
  is available under the price threshold / remaining budget, the branch
  is kept in the tree but flagged `incomplete` (useful for seeing why a
  path dead-ended) rather than silently treated as done.
- At every hop, only flights priced at or below `price_threshold` are
  kept (everything else is "trashed", per the source implementation).
- A branch (twig) is pruned entirely as soon as its cumulative cost would
  exceed `max_total_cost` - the offending flight and everything below it
  is discarded.
- Cities in `excluded_cities` (matched by IATA code or city name,
  case-insensitive) are never offered as a next stop.
- `max_branching` caps how many of the cheapest qualifying flights are
  kept at each node, so the tree doesn't explode combinatorially. Set it
  high (e.g. 999) if you truly want "keep everything under the
  threshold".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date, timedelta, datetime
from typing import Iterable, List, Optional

from flight_sources.flight_source import City, FlightSource, Flight, FlightEndpoint


@dataclass
class TripNode:
    city_code: str
    arrival_date: Date
    depth: int
    cumulative_cost: float
    flight_cost: Optional[float]      # cost of the flight that got us here (None for the root)
    remaining_days: int               # days left in the budget AT ARRIVAL, before this city's stay
    flight: Optional[Flight] = None   # the actual flight that got us here - same leg as flight_cost,
                                       # kept in full (times, airline, etc.) instead of just its price.
                                       # None for the root, same as flight_cost.
    departing_flights: List[Flight] = field(default_factory=list)
    # Every flight source.search() returned as a candidate departure from this
    # city, in the order returned (so already price-sorted / max_branching-capped
    # by the search itself) - including ones that were then rejected for
    # revisiting a city or blowing the budget, and the "fly home" leg that was
    # considered even if it didn't qualify. This is the full set of options this
    # node had; node.children is the subset that actually survived into the tree.
    path: List[str] = field(default_factory=list)   # city codes visited so far in this branch
    children: List["TripNode"] = field(default_factory=list)
    returned_home: bool = False       # True if this node IS the final "flight home" leg
    incomplete: bool = False          # True if no valid way home could be found from here

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

def split_days(days, total_days):
    k = max(1, total_days // days)      # number of chunks
    base, extra = divmod(total_days, k) # even size + leftover
    return [base] * (k - extra) + [base + 1] * extra

nights_by_city = []

def build_trip_tree(
    origin: str,
    start_date: Date,
    total_days: int,
    max_total_cost: float,
    source: FlightSource,
    nights_per_city: int = 2,
    max_branching: int = 5,
    excluded_cities: Iterable[str] = (),
) -> TripNode:
    """Build the full tree of feasible round-trip itineraries and return its root."""
    global nights_by_city

    nights_by_city = split_days(nights_per_city,total_days)
    excluded_norm = {
    code
    for e in excluded_cities
    for code in source.resolve_city(e.strip().capitalize())
    }

    if origin.strip().upper() in excluded_norm:
        raise ValueError("Cannot exclude the origin city itself.")

    root = TripNode(
        city_code=origin,
        arrival_date=start_date,
        depth=0,
        cumulative_cost=0.0,
        flight_cost=None,
        remaining_days=total_days,
        path=[origin],
    )

    price_threshold = max_total_cost / (len(nights_by_city)+1)
    # Root has no "stay" of its own - it's just the departure point, so we
    # search directly from it without consuming any days first.
    _grow_children(
        node=root,
        depart_date=start_date,
        days_available_for_stays=total_days,
        source=source,
        price_threshold=price_threshold,
        max_total_cost=max_total_cost,
        max_branching=max_branching,
        excluded_norm=excluded_norm,
        origin_city=origin
    )

    # Edge case: root itself found nowhere to go at all.
    if not root.children:
        root.incomplete = True

    return root


def _grow_children(
    node: TripNode,
    depart_date: Date,
    days_available_for_stays: int,
    source: FlightSource,
    price_threshold: float,
    max_total_cost: float,
    max_branching: int,
    excluded_norm: set,
    origin_city: str
) -> None:
    
    
    candidate_flights = source.search(
        origin=node.city_code,
        depart_date=depart_date,
        top_n=max_branching,
        visited_cities=node.path,
        price_threshold=price_threshold,
        excluded=excluded_norm
    )

    global nights_by_city
    node.departing_flights = list(candidate_flights)

    for flight in candidate_flights:
        if flight.destination.airport_code in node.path:
            if source.has_cache:
                source._cache.invalidate(node.city_code, origin_city, depart_date, node.path)
            continue  # trashed: would revisit a city already on this branch

        new_cost = node.cumulative_cost + flight.price
        if new_cost > max_total_cost:
            if source.has_cache:
                source._cache.invalidate(node.city_code, origin_city, depart_date, node.path)
            continue  # trashed: this twig would blow the total budget

        child = TripNode(
            city_code=flight.destination.airport_code,
            arrival_date=flight.destination.time,
            depth=node.depth + 1,
            cumulative_cost=new_cost,
            flight_cost=flight.price,
            flight=flight,
            remaining_days=days_available_for_stays,
            path=node.path + [flight.destination.airport_code],
        )

        price_threshold = (max_total_cost-new_cost) / (len(nights_by_city)-child.depth+1)
        node.children.append(child)
        _expand_from_city(
            child, source, price_threshold, max_total_cost,
            max_branching, excluded_norm, origin_city,flight.origin.time, nights_by_city[node.depth]
        )

    if not node.children:
        if source.has_cache:
            source._cache.invalidate(node.city_code, origin_city, depart_date, node.path)
        node.incomplete = True


def _expand_from_city(
    node: TripNode,
    source: FlightSource,
    price_threshold: float,
    max_total_cost: float,
    max_branching: int,
    excluded_norm: set,
    origin_city: str,
    departure_date: Date,
    nights_per_city: int
) -> None:
    remaining_after_this_stay = node.remaining_days - nights_per_city
    next_depart_date = node.arrival_date + timedelta(days=nights_per_city-(node.arrival_date.day - departure_date.day))

    if remaining_after_this_stay > 0:
        _grow_children(
            node=node,
            depart_date=next_depart_date,
            days_available_for_stays=remaining_after_this_stay,
            source=source,
            price_threshold=price_threshold,
            max_total_cost=max_total_cost,
            max_branching=max_branching,
            excluded_norm=excluded_norm,
            origin_city=origin_city,

        )
    # If no more days are available, node.children stays empty here and we
    # fall through to attempting the flight home below.

    if not node.children:
        _attempt_return_leg(
            node, next_depart_date, source, origin_city, price_threshold, max_total_cost,excluded_norm,
        )


def _attempt_return_leg(
    node: TripNode,
    depart_date: Date,
    source: FlightSource,
    origin_city: str,
    price_threshold: float,
    max_total_cost: float,
    excluded_norm: Iterable[str]
) -> None:
    home_flight = None
    if source.has_cache:
        if source._cache.is_invalid(node.city_code,origin_city,depart_date,node.path):
            node.incomplete = True
            return
        flights: list[Flight] = source._cache.load(node.city_code, depart_date) or []
        if len(flights) > 0:
            home_flight = flights[0]

    if not home_flight:
        home_flight: Flight = source.get_home_flight(node.city_code, origin_city, depart_date, max_total_cost - node.cumulative_cost, excluded_norm)
    if source.has_cache:
        if not home_flight:
            home_flight = Flight(FlightEndpoint(node.city_code, datetime.now()), FlightEndpoint(origin_city, datetime.now()),1000,"","","")
        if not contains_flight(flights, home_flight):
            flights.append(home_flight)
            source._cache.save(node.city_code, depart_date, flights)

    if home_flight is not None:
        node.departing_flights.append(home_flight)

    if home_flight.price is None or home_flight.price > price_threshold:
        node.incomplete = True
        if source.has_cache:
            source._cache.invalidate(node.city_code, origin_city, depart_date, node.path)
        return

    new_cost = node.cumulative_cost + home_flight.price
    if new_cost > max_total_cost:
        node.incomplete = True
        if source.has_cache:
            source._cache.invalidate(node.city_code, origin_city, depart_date, node.path)
        return

    home_leg = TripNode(
        city_code=origin_city,
        arrival_date=depart_date,
        depth=node.depth + 1,
        cumulative_cost=new_cost,
        flight_cost=home_flight.price,
        flight=home_flight,
        remaining_days=0,
        path=node.path + [origin_city],
        returned_home=True,
    )
    node.children.append(home_leg)
    node.incomplete = False

def contains_flight(flights: list[Flight], flight: Flight):
    for f in flights:
        if f.destination.airport_code == flight.destination.airport_code and f.origin.airport_code == flight.origin.airport_code:
            return True

    return False

def iter_itineraries(root: TripNode):
    """Yield (city_path, flight_costs, total_cost) for every COMPLETE (returned home) itinerary."""

    def _walk(node: TripNode, costs: List[float]):
        if node.returned_home:
            yield list(node.path), list(costs), node.cumulative_cost
            return
        for child in node.children:
            yield from _walk(child, costs + [child.flight_cost])

    yield from _walk(root, [])


def iter_incomplete_branches(root: TripNode):
    """Yield TripNodes where no affordable flight home could be found - useful for debugging."""

    def _walk(node: TripNode):
        if node.incomplete and node.is_leaf:
            yield node
        for child in node.children:
            yield from _walk(child)

    yield from _walk(root)


def count_nodes(root: TripNode) -> int:
    return 1 + sum(count_nodes(c) for c in root.children)
