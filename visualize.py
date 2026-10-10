"""
Renders a TripNode tree as an interactive HTML page: nodes = cities
(with name/country), edges = flights, labeled with their price and
each node labeled with its departure date.

The markup/CSS/JS lives in trip_tree_template.html, next to this file.
draw_trip_tree() fills in a few __PLACEHOLDER__ tokens in that template
and writes the result to output_path - it's a self-contained page
(D3.js loaded from a CDN, everything else inlined) rather than a
static image, because two of its features only make sense interactively:

  - Collapsible branches: click any card with a chevron to fold/unfold
    its subtree. "Collapse all" / "Expand all" / "Fit view" buttons sit
    in the header for quickly taming a wide tree.
  - Horizontal, left-to-right layout: the origin sits on the left and
    each flight moves the tree one column to the right, which reads
    better than top-down once there are more than a few branches.

Dead ends are dropped entirely rather than drawn in red: any branch
that never reaches a completed itinerary (no affordable flight home
was found) is pruned out of the tree before it's rendered, so the page
only shows city paths that actually got somewhere.

Node colors:
  navy - the root / origin
  blue - an intermediate city, still mid-trip
  gold - the final "flight home" leg (a completed, valid itinerary)
"""

from __future__ import annotations

import html as html_lib
import json
from datetime import date, datetime
from pathlib import Path

from tree_builder import TripNode
from flight_sources.flight_source import FlightSource

COLOR_ROOT = "#2c3e50"
COLOR_MID = "#2980b9"
COLOR_RETURNED = "#d4ac0d"

_TEMPLATE_PATH = Path(__file__).with_name("trip_tree_template.html")


def _format_date(value) -> str:
    """Formats a date or datetime as e.g. '14 Mar 2027' (adding a time when
    the value is a full datetime, e.g. '14 Mar 2027, 08:15'); falls back to
    str() for anything else so an unexpected type doesn't crash the build."""
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def _prune_and_serialize(node: TripNode, source: FlightSource) -> dict | None:
    """
    Recursively converts a TripNode into a plain dict for the front-end,
    dropping any branch that never reaches a completed itinerary.

    Returns None if this node is itself a dead end (a leaf with no
    flight home found), or if every branch beneath it died out.
    """
    if node.is_leaf:
        if not node.returned_home:
            return None
        children_data: list[dict] = []
    else:
        children_data = [_prune_and_serialize(c, source) for c in node.children]
        children_data = [c for c in children_data if c is not None]
        if not children_data and not node.returned_home:
            return None

    if node.depth == 0:
        status, status_label = "root", "Origin"
    elif node.returned_home:
        status, status_label = "returned", "Flight home"
    else:
        status, status_label = "mid", None

    # `node.flight` is the actual flight that landed on this node (set by
    # tree_builder for everything except the root, which has none). Its two
    # endpoints give us the exact departure and arrival times for this leg.
    flight = getattr(node, "flight", None)
    departure_date = flight.origin.time if flight is not None else None
    arrival_date = flight.destination.time if flight is not None else None

    return {
        "code": node.city_code,
        "city": source.resolve_code(node.city_code),
        "cost": round(node.cumulative_cost),
        "flightCost": round(node.flight_cost) if node.depth > 0 else None,
        "departureDate": _format_date(departure_date) if departure_date is not None else None,
        "arrivalDate": _format_date(arrival_date) if arrival_date is not None else None,
        "status": status,
        "statusLabel": status_label,
        "children": children_data,
    }


def draw_trip_tree(
    root: TripNode,
    source: FlightSource,
    output_path: str = "trip_tree.html",
    title: str = "Flight tree",
    origin_departure_date=None,
) -> str:
    """
    origin_departure_date: the date the trip starts (the same `start_date`
    you passed to build_trip_tree). The root's `flight` is always None -
    there's no incoming leg to read a date off - so pass it here and it'll
    show on the root's card. Omit it and the root just won't have a date.
    """
    tree_data = _prune_and_serialize(root, source)
    if tree_data is None:
        # Nothing ever made it home - show just the origin so the page
        # still renders instead of erroring out on an empty tree.
        tree_data = {
            "code": root.city_code,
            "city": source.resolve_code(root.city_code),
            "cost": round(root.cumulative_cost),
            "flightCost": None,
            "departureDate": None,
            "arrivalDate": None,
            "status": "root",
            "statusLabel": "Origin",
            "children": [],
        }

    if origin_departure_date is not None:
        tree_data["departureDate"] = _format_date(origin_departure_date)

    data_json = json.dumps(tree_data).replace("</", "<\\/")

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    page = (
        template
        .replace("__TITLE__", html_lib.escape(title))
        .replace("__TREE_DATA_JSON__", data_json)
        .replace("__COLOR_ROOT__", COLOR_ROOT)
        .replace("__COLOR_MID__", COLOR_MID)
        .replace("__COLOR_RETURNED__", COLOR_RETURNED)
    )

    Path(output_path).write_text(page, encoding="utf-8")
    return output_path
