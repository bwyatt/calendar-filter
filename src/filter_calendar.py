#!/usr/bin/env python3
"""
filter_calendar.py — Fetch one or more iCal feeds, apply configurable filters,
and write a single combined output feed.

Usage:
    python filter_calendar.py [config.yml] [output/filtered.ics]
"""

import logging
import re
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Optional

import requests
import yaml
from dateutil import parser as dateutil_parser
from icalendar import Calendar, Event

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def load_config(config_path: str) -> dict:
    """Load and return the YAML configuration file."""
    with open(config_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Calendar fetching
# ---------------------------------------------------------------------------


def fetch_calendar(url: str, timeout: int = 30) -> Calendar:
    """Fetch an iCal feed from *url* and return a parsed Calendar object."""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return Calendar.from_ical(response.content)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def get_str(component, prop_name: str) -> str:
    """Return a property value as a plain string, or '' if missing."""
    val = component.get(prop_name)
    if val is None:
        return ""
    return str(val)


def matches_patterns(value: str, patterns: list) -> bool:
    """Return True if *value* matches at least one regex in *patterns*."""
    for pattern in patterns:
        if re.search(pattern, value, re.IGNORECASE):
            return True
    return False


def is_all_day(component) -> bool:
    """Return True when the event's DTSTART is a bare date (not datetime)."""
    dtstart = component.get("DTSTART")
    if dtstart is None:
        return False
    return isinstance(dtstart.dt, date) and not isinstance(dtstart.dt, datetime)


def get_event_datetime(component, prop: str) -> Optional[datetime]:
    """Return the DTSTART or DTEND of *component* as a timezone-aware datetime.

    All-day events (bare dates) are treated as midnight UTC on that date.
    Naive datetimes are assumed to be UTC.
    """
    val = component.get(prop)
    if val is None:
        return None
    dt = val.dt
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.min, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_free_busy(component) -> str:
    """Return 'FREE' or 'BUSY' based on the event's TRANSP property.

    iCal spec: TRANSPARENT → FREE, OPAQUE (default) → BUSY.
    """
    transp = get_str(component, "TRANSP").upper()
    return "FREE" if transp == "TRANSPARENT" else "BUSY"


# ---------------------------------------------------------------------------
# Filter application
# ---------------------------------------------------------------------------


def _apply_text_filter(value: str, filter_cfg: Optional[dict]) -> bool:
    """Apply a text filter (include/exclude regex lists) to *value*.

    Rules:
    - If ``include`` is non-empty the value must match at least one pattern.
    - If ``exclude`` is non-empty the value must not match any pattern.
    - Both rules are applied; both must pass for the event to be kept.
    """
    if not filter_cfg:
        return True
    include = filter_cfg.get("include") or []
    exclude = filter_cfg.get("exclude") or []

    if include and not matches_patterns(value, include):
        return False
    if exclude and matches_patterns(value, exclude):
        return False
    return True


def _apply_datetime_filter(dt: Optional[datetime], filter_cfg: Optional[dict]) -> bool:
    """Return True when *dt* falls within the bounds specified by *filter_cfg*.

    Accepted keys: ``after`` and ``before`` (ISO 8601 strings or bare dates).
    Naive strings are treated as UTC.
    """
    if not filter_cfg or dt is None:
        return True

    def _parse(s: str) -> datetime:
        parsed = dateutil_parser.parse(str(s))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    after = filter_cfg.get("after")
    before = filter_cfg.get("before")

    if after and dt < _parse(after):
        return False
    if before and dt >= _parse(before):
        return False
    return True


def apply_filters(component, filters: dict) -> bool:
    """Return True when *component* passes all active filters.

    Supported filter keys:
    - ``title``       — text filter on SUMMARY
    - ``description`` — text filter on DESCRIPTION
    - ``location``    — text filter on LOCATION
    - ``creator``     — text filter on ORGANIZER
    - ``free_busy``   — list of allowed statuses: ``["BUSY"]``, ``["FREE"]``,
                        or ``["BUSY", "FREE"]`` (default, same as omitting)
    - ``all_day``     — ``true`` include only all-day events; ``false`` exclude
                        all-day events; omit (or ``null``) to include both
    - ``start_time``  — datetime filter on DTSTART (keys: ``after``, ``before``)
    - ``end_time``    — datetime filter on DTEND   (keys: ``after``, ``before``)
    """
    if not filters:
        return True

    # --- text fields ---
    if not _apply_text_filter(get_str(component, "SUMMARY"), filters.get("title")):
        return False
    if not _apply_text_filter(get_str(component, "DESCRIPTION"), filters.get("description")):
        return False
    if not _apply_text_filter(get_str(component, "LOCATION"), filters.get("location")):
        return False
    if not _apply_text_filter(get_str(component, "ORGANIZER"), filters.get("creator")):
        return False

    # --- free/busy ---
    free_busy_cfg = filters.get("free_busy")
    if free_busy_cfg:
        allowed = [s.upper() for s in free_busy_cfg]
        if get_free_busy(component) not in allowed:
            return False

    # --- all-day ---
    all_day_cfg = filters.get("all_day")
    if all_day_cfg is not None:
        if bool(all_day_cfg) != is_all_day(component):
            return False

    # --- datetime bounds ---
    if not _apply_datetime_filter(get_event_datetime(component, "DTSTART"), filters.get("start_time")):
        return False
    if not _apply_datetime_filter(get_event_datetime(component, "DTEND"), filters.get("end_time")):
        return False

    return True


# ---------------------------------------------------------------------------
# Filter merging
# ---------------------------------------------------------------------------


def merge_filters(global_filters: dict, calendar_filters: dict) -> dict:
    """Merge *global_filters* with *calendar_filters*.

    Per-calendar filter keys override global filter keys of the same name.
    Keys present only in one source are kept as-is.
    """
    if not global_filters:
        return dict(calendar_filters) if calendar_filters else {}
    if not calendar_filters:
        return dict(global_filters)
    merged = dict(global_filters)
    merged.update(calendar_filters)
    return merged


# ---------------------------------------------------------------------------
# Busy-only output helper
# ---------------------------------------------------------------------------


def make_busy_event(component) -> Event:
    """Return a redacted VEVENT retaining only UID, DTSTART, and DTEND.

    SUMMARY is set to "Busy"; all other properties are omitted.
    """
    new_event = Event()
    new_event.add("SUMMARY", "Busy")
    if "UID" in component:
        new_event.add("UID", component["UID"])
    if "DTSTART" in component:
        new_event.add("DTSTART", component["DTSTART"].dt)
    if "DTEND" in component:
        new_event.add("DTEND", component["DTEND"].dt)
    return new_event


# ---------------------------------------------------------------------------
# Main processing pipeline
# ---------------------------------------------------------------------------


def process_calendars(config: dict) -> Calendar:
    """Fetch, filter, and combine all configured calendars into one Calendar."""
    output_cal = Calendar()
    output_cal.add("PRODID", "-//calendar-filter//github.com//EN")
    output_cal.add("VERSION", "2.0")
    output_cal.add("CALSCALE", "GREGORIAN")
    output_cal.add("METHOD", "PUBLISH")

    busy_only = config.get("output", {}).get("busy_only", False)
    global_filters = config.get("global_filters") or {}
    calendars = config.get("calendars") or []

    for cal_config in calendars:
        url = cal_config.get("url", "")
        name = cal_config.get("name", url)
        cal_filters = cal_config.get("filters") or {}

        filters = merge_filters(global_filters, cal_filters)

        log.info("Fetching calendar: %s", name)
        try:
            cal = fetch_calendar(url)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to fetch calendar '%s': %s", name, exc)
            continue

        included = 0
        skipped = 0
        for component in cal.walk():
            if component.name != "VEVENT":
                continue
            if apply_filters(component, filters):
                event = make_busy_event(component) if busy_only else component
                output_cal.add_component(event)
                included += 1
            else:
                skipped += 1

        log.info("  %s — included: %d, skipped: %d", name, included, skipped)

    return output_cal


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yml"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/filtered.ics"

    config = load_config(config_path)
    output_cal = process_calendars(config)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(output_cal.to_ical())
    log.info("Output written to %s", output_path)


if __name__ == "__main__":
    main()
