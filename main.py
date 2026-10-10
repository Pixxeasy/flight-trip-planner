"""
Flight-tree trip planner - CLI entry point (Typer version).

Anything you don't pass as a flag is asked interactively. Pass everything
(or use --yes) and it runs without prompts.

    python main.py                                   # prompts for everything required
    python main.py --origin BER --start-date 2026-06-01 --total-days 10 \\
        --price-threshold 120 --max-total-cost 400   # then one checklist for the rest
    python main.py --origin BER --start-date 2026-06-01 --total-days 10 \\
        --price-threshold 120 --max-total-cost 400 --source mock \\
        --exclude CDG --exclude Rome --yes           # no prompts at all

Every itinerary in the tree ends with a flight back to --origin.
Output: a PNG of the tree plus a ranked list of complete itineraries.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from enum import Enum
from types import SimpleNamespace
from typing import List, Optional

import click
import questionary
import typer

from flight_sources.skyscanner import SkyscannerScraperSource
from flight_sources.kiwi import KiwiTequilaSource
from flight_sources.mock import MockFlightSource
from flight_sources.flight_cache import FlightCache, CachingFlightSource
from flight_sources.flight_source import FlightSource
from tree_builder import build_trip_tree, iter_itineraries, iter_incomplete_branches, count_nodes
from visualize import draw_trip_tree

app = typer.Typer(add_completion=False)

DATE_FORMATS = ["%d.%m.%Y", "%Y-%m-%d"]  # 01.06.2026 or 2026-06-01
TIME_FORMATS = ["%H:%M"]                  # 6:30, 06:30, 23:59


class Source(str, Enum):
    mock = "mock"
    kiwi = "kiwi"
    skyscanner = "skyscanner"


# --------------------------------------------------------------------------
# Argument handling: which are required, what the defaults are
# --------------------------------------------------------------------------

REQUIRED = ["origin", "start_date", "total_days", "max_total_cost"]
DEFAULTS = {
    "nights_per_city": 2,
    "max_branching": 5,
    "exclude": [],
    "source": "mock",
    "earliest_departure": "00:00",
    "latest_departure": "23:59",
    "output": "trip_tree.html",
    "top_itineraries": 10,
}
# kiwi_api_key is neither: it's only needed when source == kiwi (handled in collect)


def _empty(v) -> bool:
    return v is None or v == [] or v == ()


def _norm(v):
    return v.value if isinstance(v, Enum) else v


def _label(p: click.Parameter) -> str:
    return getattr(p, "help", None) or p.name.replace("_", " ")


def ask(p: click.Parameter, default=None):
    """Build the right questionary prompt from a Typer/click parameter."""
    msg = f"{_label(p)}:"

    if p.multiple:
        r = questionary.text(msg + " (comma-separated, blank for none)",
                             default=",".join(default or [])).ask()
        r = None if r is None else [s.strip() for s in r.split(",") if s.strip()]
    elif isinstance(p.type, click.Choice):
        r = questionary.select(msg, choices=list(p.type.choices), default=default).ask()
    else:
        # Generic text prompt; validate and convert with the param's own type
        # (int, float, datetime, str, ...), so it behaves exactly like the flag.
        def check(s: str):
            try:
                p.type.convert(s, None, None)
                return True
            except Exception as e:  # never let a validator crash the prompt
                return e.format_message() if isinstance(e, click.ClickException) else str(e)

        # validate_while_typing=False: only check on Enter, not on every keystroke
        r = questionary.text(msg, default="" if default is None else str(default),
                             validate=check, validate_while_typing=False).ask()
        r = None if r is None else p.type.convert(r, None, None)

    if r is None:  # Ctrl+C
        raise typer.Exit(1)
    return r


def collect(ctx: typer.Context, no_prompt: bool) -> SimpleNamespace:
    params = {p.name: p for p in ctx.command.params if p.name != "yes"}
    values = {n: _norm(ctx.params.get(n)) for n in params}

    # 1. Required args: prompt, or fail in non-interactive mode
    for n in REQUIRED:
        if _empty(values[n]):
            if no_prompt:
                raise typer.BadParameter(f"--{n.replace('_', '-')} is required", ctx=ctx)
            values[n] = ask(params[n])

    # 2. Optional args: apply defaults, then one checklist to change any
    optional = [n for n in DEFAULTS if _empty(values[n])]
    for n in optional:
        values[n] = DEFAULTS[n]

    if optional and not no_prompt:
        picked = questionary.checkbox(
            "Change any options? (space to select, enter to continue)",
            choices=[questionary.Choice(f"{_label(params[n])}  [{values[n]}]", value=n)
                     for n in optional],
        ).ask()
        if picked is None:
            raise typer.Exit(1)
        for n in picked:
            values[n] = ask(params[n], values[n])

    # 3. Conditional: Kiwi needs an API key
    if values["source"] == "kiwi" and _empty(values["kiwi_api_key"]):
        if no_prompt:
            raise typer.BadParameter("--kiwi-api-key is required when --source kiwi", ctx=ctx)
        values["kiwi_api_key"] = ask(params["kiwi_api_key"])

    values["start_date"] = values["start_date"].date()

    # Departure window: datetime (from flag/prompt) or "HH:MM" string (default) -> time
    for n in ("earliest_departure", "latest_departure"):
        v = values[n]
        values[n] = v.time() if isinstance(v, datetime) else datetime.strptime(v, "%H:%M").time()
    if values["earliest_departure"] > values["latest_departure"]:
        raise typer.BadParameter("--earliest-departure must not be after --latest-departure", ctx=ctx)
    return SimpleNamespace(**values)


# --------------------------------------------------------------------------
# Original logic (unchanged apart from start_date already being a date)
# --------------------------------------------------------------------------

def build_source(args: SimpleNamespace) -> CachingFlightSource:
    cache = build_cache(args)

    source: FlightSource
    if args.source == "mock":
        source = MockFlightSource()
    elif args.source == "kiwi":
        source = KiwiTequilaSource(api_key=args.kiwi_api_key)
    else:  # skyscanner
        source = SkyscannerScraperSource(
            earliest_departure=args.earliest_departure,
            latest_departure=args.latest_departure,
        )
        source.set_airport_source("./airport_sources/airports.csv")
        source.airport_source.read()

    return CachingFlightSource(source, cache)


def build_cache(args) -> FlightCache:
    return FlightCache(args.origin, args.start_date, args.total_days, args.nights_per_city, args.earliest_departure, args.latest_departure)


def get_itinary_treshhold(price_treshhold, total_days, nights_per_city):
    return (math.ceil(total_days / nights_per_city) + 1) * price_treshhold


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

@app.command(help="Build a tree of cheap multi-city round-trip flight itineraries.")
def main(
    ctx: typer.Context,
    origin: Optional[str] = typer.Option(None, help="Departure airport/city code, e.g. BER"),
    start_date: Optional[datetime] = typer.Option(None, formats=DATE_FORMATS, help="Start date (DD.MM.YYYY or YYYY-MM-DD)"),
    total_days: Optional[int] = typer.Option(None, help="Total trip length in days"),
    nights_per_city: Optional[int] = typer.Option(None, help="Nights per city (default 2)"),
    max_total_cost: Optional[float] = typer.Option(None, help="Max cumulative cost before a twig is pruned"),
    max_branching: Optional[int] = typer.Option(None, help="Max cheapest flights kept per node (default 5)"),
    exclude: Optional[List[str]] = typer.Option(
        None, "--exclude", "-x", help="City names or IATA codes to never visit (repeatable)"),
    earliest_departure: Optional[datetime] = typer.Option(
        None, formats=TIME_FORMATS, help="Earliest departure time HH:MM (default 00:00)"),
    latest_departure: Optional[datetime] = typer.Option(
        None, formats=TIME_FORMATS, help="Latest departure time HH:MM (default 23:59)"),
    source: Optional[Source] = typer.Option(None, help="Flight data source"),
    kiwi_api_key: Optional[str] = typer.Option(None, help="Kiwi API key (needed if --source kiwi)"),
    output: Optional[str] = typer.Option(None, help="Output PNG path"),
    top_itineraries: Optional[int] = typer.Option(None, help="How many cheapest itineraries to print"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Never prompt; use defaults"),
):
    args = collect(ctx, no_prompt=yes or not sys.stdin.isatty())
    src = build_source(args)

    root = build_trip_tree(
        origin=args.origin.upper(),
        start_date=args.start_date,
        total_days=args.total_days,
        max_total_cost=args.max_total_cost,
        source=src,
        nights_per_city=args.nights_per_city,
        max_branching=args.max_branching,
        excluded_cities=args.exclude,
    )

    print(f"Tree built: {count_nodes(root)} nodes total.\n")

    itineraries = sorted(iter_itineraries(root), key=lambda t: t[2])
    print(f"Top {min(args.top_itineraries, len(itineraries))} cheapest complete (round-trip) itineraries:\n")
    for path, costs, total in itineraries[: args.top_itineraries]:
        legs = " -> ".join(path)
        cost_str = ", ".join(f"\u20ac{c:.0f}" for c in costs)
        print(f"  {legs}\n    flights: [{cost_str}]  total: \u20ac{total:.2f}\n")

    dead_ends = list(iter_incomplete_branches(root))
    if dead_ends:
        print(f"({len(dead_ends)} branch(es) dead-ended with no affordable flight home - shown in red in the image.)\n")

    out_path = draw_trip_tree(
        root, output_path=args.output,
        title=f"Flight tree from {args.origin.upper()} starting {args.start_date}",
        source=src,
    )
    print(f"Saved tree visualization to {out_path}")


if __name__ == "__main__":
    app()