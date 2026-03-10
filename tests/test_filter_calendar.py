"""
Unit tests for src/filter_calendar.py

Run with:  pytest tests/ -v
"""

import sys
import os
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from icalendar import Calendar, Event, vDatetime, vDate

# Make src/ importable without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from filter_calendar import (  # noqa: E402
    _apply_datetime_filter,
    _apply_text_filter,
    apply_filters,
    fetch_calendar,
    get_event_datetime,
    get_free_busy,
    is_all_day,
    make_busy_event,
    merge_filters,
    process_calendars,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:test-uid-1@example.com
SUMMARY:Team Meeting
DESCRIPTION:Weekly sync
LOCATION:Conference Room A
ORGANIZER:mailto:alice@example.com
DTSTART:20260310T090000Z
DTEND:20260310T100000Z
TRANSP:OPAQUE
END:VEVENT
END:VCALENDAR
"""

ALL_DAY_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:all-day-1@example.com
SUMMARY:Company Holiday
DTSTART;VALUE=DATE:20260315
DTEND;VALUE=DATE:20260316
END:VEVENT
END:VCALENDAR
"""

FREE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:free-1@example.com
SUMMARY:Out of Office
DTSTART:20260310T140000Z
DTEND:20260310T150000Z
TRANSP:TRANSPARENT
END:VEVENT
END:VCALENDAR
"""


def _make_event(
    summary="Test Event",
    description="",
    location="",
    organizer="",
    transp="OPAQUE",
    dtstart=None,
    dtend=None,
    all_day=False,
) -> Event:
    """Build a simple VEVENT component for testing."""
    ev = Event()
    ev.add("SUMMARY", summary)
    if description:
        ev.add("DESCRIPTION", description)
    if location:
        ev.add("LOCATION", location)
    if organizer:
        ev.add("ORGANIZER", organizer)
    ev.add("TRANSP", transp)
    if all_day:
        ev.add("DTSTART", date(2026, 3, 15))
        ev.add("DTEND", date(2026, 3, 16))
    else:
        ev.add("DTSTART", dtstart or datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc))
        ev.add("DTEND", dtend or datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc))
    return ev


# ---------------------------------------------------------------------------
# _apply_text_filter
# ---------------------------------------------------------------------------


class TestApplyTextFilter:
    def test_no_filter_passes(self):
        assert _apply_text_filter("anything", None) is True
        assert _apply_text_filter("anything", {}) is True

    def test_include_match(self):
        assert _apply_text_filter("Team Meeting", {"include": ["Team"]}) is True

    def test_include_no_match(self):
        assert _apply_text_filter("Lunch", {"include": ["Team"]}) is False

    def test_exclude_match(self):
        assert _apply_text_filter("Cancelled Sprint", {"exclude": ["Cancel"]}) is False

    def test_exclude_no_match(self):
        assert _apply_text_filter("Active Sprint", {"exclude": ["Cancel"]}) is True

    def test_include_and_exclude(self):
        cfg = {"include": ["Sprint"], "exclude": ["Cancelled"]}
        assert _apply_text_filter("Sprint Planning", cfg) is True
        assert _apply_text_filter("Cancelled Sprint", cfg) is False
        assert _apply_text_filter("Lunch Break", cfg) is False

    def test_case_insensitive(self):
        assert _apply_text_filter("TEAM MEETING", {"include": ["team meeting"]}) is True

    def test_regex_pattern(self):
        assert _apply_text_filter("Sprint 42", {"include": ["Sprint \\d+"]}) is True
        assert _apply_text_filter("Sprint Alpha", {"include": ["Sprint \\d+"]}) is False

    def test_empty_include_list_is_ignored(self):
        assert _apply_text_filter("anything", {"include": []}) is True

    def test_empty_exclude_list_is_ignored(self):
        assert _apply_text_filter("anything", {"exclude": []}) is True


# ---------------------------------------------------------------------------
# is_all_day / get_free_busy / get_event_datetime
# ---------------------------------------------------------------------------


class TestEventHelpers:
    def test_is_all_day_true(self):
        ev = _make_event(all_day=True)
        assert is_all_day(ev) is True

    def test_is_all_day_false(self):
        ev = _make_event()
        assert is_all_day(ev) is False

    def test_get_free_busy_opaque(self):
        ev = _make_event(transp="OPAQUE")
        assert get_free_busy(ev) == "BUSY"

    def test_get_free_busy_transparent(self):
        ev = _make_event(transp="TRANSPARENT")
        assert get_free_busy(ev) == "FREE"

    def test_get_free_busy_default_is_busy(self):
        ev = Event()
        ev.add("SUMMARY", "No transp")
        assert get_free_busy(ev) == "BUSY"

    def test_get_event_datetime_timed(self):
        dt = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        ev = _make_event(dtstart=dt)
        result = get_event_datetime(ev, "DTSTART")
        assert result == dt

    def test_get_event_datetime_all_day(self):
        ev = _make_event(all_day=True)
        result = get_event_datetime(ev, "DTSTART")
        assert isinstance(result, datetime)
        assert result.year == 2026 and result.month == 3 and result.day == 15

    def test_get_event_datetime_missing_prop(self):
        ev = Event()
        assert get_event_datetime(ev, "DTSTART") is None


# ---------------------------------------------------------------------------
# _apply_datetime_filter
# ---------------------------------------------------------------------------


class TestApplyDatetimeFilter:
    def test_no_filter(self):
        dt = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(dt, None) is True
        assert _apply_datetime_filter(dt, {}) is True

    def test_after_passes(self):
        dt = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(dt, {"after": "2026-03-01T00:00:00Z"}) is True

    def test_after_fails(self):
        dt = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(dt, {"after": "2026-04-01T00:00:00Z"}) is False

    def test_before_passes(self):
        dt = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(dt, {"before": "2026-04-01T00:00:00Z"}) is True

    def test_before_fails_equal(self):
        dt = datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(dt, {"before": "2026-04-01T00:00:00Z"}) is False

    def test_before_fails_after(self):
        dt = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(dt, {"before": "2026-04-01T00:00:00Z"}) is False

    def test_after_and_before(self):
        dt = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        cfg = {"after": "2026-03-01T00:00:00Z", "before": "2026-04-01T00:00:00Z"}
        assert _apply_datetime_filter(dt, cfg) is True
        out_of_range = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)
        assert _apply_datetime_filter(out_of_range, cfg) is False


# ---------------------------------------------------------------------------
# apply_filters (integration of all filter types)
# ---------------------------------------------------------------------------


class TestApplyFilters:
    def test_no_filters(self):
        ev = _make_event()
        assert apply_filters(ev, {}) is True

    def test_title_filter(self):
        ev = _make_event(summary="Team Meeting")
        assert apply_filters(ev, {"title": {"include": ["Team"]}}) is True
        assert apply_filters(ev, {"title": {"include": ["Lunch"]}}) is False
        assert apply_filters(ev, {"title": {"exclude": ["Team"]}}) is False

    def test_description_filter(self):
        ev = _make_event(description="confidential notes")
        assert apply_filters(ev, {"description": {"exclude": ["confidential"]}}) is False
        assert apply_filters(ev, {"description": {"exclude": ["public"]}}) is True

    def test_location_filter(self):
        ev = _make_event(location="Building 2, Room 201")
        assert apply_filters(ev, {"location": {"include": ["Building 2"]}}) is True
        assert apply_filters(ev, {"location": {"include": ["Building 3"]}}) is False

    def test_creator_filter(self):
        ev = _make_event(organizer="mailto:alice@example.com")
        assert apply_filters(ev, {"creator": {"include": ["alice@example.com"]}}) is True
        assert apply_filters(ev, {"creator": {"include": ["bob@example.com"]}}) is False

    def test_free_busy_filter_busy(self):
        busy_ev = _make_event(transp="OPAQUE")
        free_ev = _make_event(transp="TRANSPARENT")
        assert apply_filters(busy_ev, {"free_busy": ["BUSY"]}) is True
        assert apply_filters(free_ev, {"free_busy": ["BUSY"]}) is False

    def test_free_busy_filter_free(self):
        free_ev = _make_event(transp="TRANSPARENT")
        assert apply_filters(free_ev, {"free_busy": ["FREE"]}) is True

    def test_free_busy_filter_both(self):
        ev = _make_event(transp="OPAQUE")
        assert apply_filters(ev, {"free_busy": ["BUSY", "FREE"]}) is True

    def test_all_day_true(self):
        ev_all_day = _make_event(all_day=True)
        ev_timed = _make_event()
        assert apply_filters(ev_all_day, {"all_day": True}) is True
        assert apply_filters(ev_timed, {"all_day": True}) is False

    def test_all_day_false(self):
        ev_all_day = _make_event(all_day=True)
        ev_timed = _make_event()
        assert apply_filters(ev_all_day, {"all_day": False}) is False
        assert apply_filters(ev_timed, {"all_day": False}) is True

    def test_all_day_null(self):
        ev_all_day = _make_event(all_day=True)
        ev_timed = _make_event()
        assert apply_filters(ev_all_day, {"all_day": None}) is True
        assert apply_filters(ev_timed, {"all_day": None}) is True

    def test_start_time_filter(self):
        ev = _make_event(dtstart=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc))
        assert apply_filters(ev, {"start_time": {"after": "2026-03-01T00:00:00Z"}}) is True
        assert apply_filters(ev, {"start_time": {"after": "2026-04-01T00:00:00Z"}}) is False

    def test_end_time_filter(self):
        ev = _make_event(dtend=datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc))
        assert apply_filters(ev, {"end_time": {"before": "2026-04-01T00:00:00Z"}}) is True
        assert apply_filters(ev, {"end_time": {"before": "2026-03-01T00:00:00Z"}}) is False

    def test_combined_filters(self):
        ev = _make_event(
            summary="Team Meeting",
            description="weekly sync",
            location="Building 2",
            organizer="mailto:alice@example.com",
            transp="OPAQUE",
            dtstart=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        )
        filters = {
            "title": {"include": ["Team"]},
            "description": {"exclude": ["confidential"]},
            "location": {"include": ["Building"]},
            "creator": {"include": ["alice"]},
            "free_busy": ["BUSY"],
            "all_day": False,
            "start_time": {"after": "2026-01-01T00:00:00Z"},
        }
        assert apply_filters(ev, filters) is True

    def test_non_vevent_components_not_filtered_by_caller(self):
        # apply_filters is called only on VEVENT; ensure it doesn't crash
        # if called with a minimal event missing most fields
        ev = Event()
        ev.add("SUMMARY", "Bare event")
        assert apply_filters(ev, {"title": {"include": ["Bare"]}}) is True


# ---------------------------------------------------------------------------
# merge_filters
# ---------------------------------------------------------------------------


class TestMergeFilters:
    def test_both_empty(self):
        assert merge_filters({}, {}) == {}

    def test_global_only(self):
        g = {"title": {"exclude": ["Private"]}}
        assert merge_filters(g, {}) == g

    def test_calendar_only(self):
        c = {"title": {"include": ["Work"]}}
        assert merge_filters({}, c) == c

    def test_calendar_overrides_global(self):
        g = {"title": {"exclude": ["Private"]}, "all_day": False}
        c = {"title": {"include": ["Sprint"]}}
        merged = merge_filters(g, c)
        # calendar's title overrides global's title
        assert merged["title"] == {"include": ["Sprint"]}
        # global's all_day is preserved
        assert merged["all_day"] is False

    def test_does_not_mutate_inputs(self):
        g = {"title": {"exclude": ["Private"]}}
        c = {"title": {"include": ["Work"]}}
        merge_filters(g, c)
        assert g == {"title": {"exclude": ["Private"]}}
        assert c == {"title": {"include": ["Work"]}}


# ---------------------------------------------------------------------------
# make_busy_event
# ---------------------------------------------------------------------------


class TestMakeBusyEvent:
    def test_summary_is_busy(self):
        ev = _make_event(summary="Secret Meeting", description="Confidential", location="HQ")
        busy = make_busy_event(ev)
        assert str(busy.get("SUMMARY")) == "Busy"

    def test_dtstart_dtend_preserved(self):
        start = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc)
        ev = _make_event(dtstart=start, dtend=end)
        busy = make_busy_event(ev)
        assert busy.get("DTSTART").dt == start
        assert busy.get("DTEND").dt == end

    def test_other_properties_stripped(self):
        ev = _make_event(description="Secret", location="HQ", organizer="bob@example.com")
        busy = make_busy_event(ev)
        assert busy.get("DESCRIPTION") is None
        assert busy.get("LOCATION") is None
        assert busy.get("ORGANIZER") is None

    def test_uid_preserved(self):
        ev = _make_event()
        ev.add("UID", "test-uid-42@example.com")
        busy = make_busy_event(ev)
        assert str(busy.get("UID")) == "test-uid-42@example.com"


# ---------------------------------------------------------------------------
# fetch_calendar
# ---------------------------------------------------------------------------


class TestFetchCalendar:
    def test_successful_fetch(self):
        mock_response = MagicMock()
        mock_response.content = MINIMAL_ICS
        mock_response.raise_for_status = MagicMock()
        with patch("filter_calendar.requests.get", return_value=mock_response) as mock_get:
            cal = fetch_calendar("https://example.com/cal.ics")
            mock_get.assert_called_once_with("https://example.com/cal.ics", timeout=30)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1
        assert str(events[0].get("SUMMARY")) == "Team Meeting"

    def test_http_error_propagates(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")
        with patch("filter_calendar.requests.get", return_value=mock_response):
            with pytest.raises(Exception, match="404"):
                fetch_calendar("https://example.com/missing.ics")


# ---------------------------------------------------------------------------
# process_calendars
# ---------------------------------------------------------------------------


class TestProcessCalendars:
    def _mock_get(self, content: bytes):
        mock_response = MagicMock()
        mock_response.content = content
        mock_response.raise_for_status = MagicMock()
        return mock_response

    def test_basic_pass_through(self):
        config = {
            "output": {"busy_only": False},
            "global_filters": {},
            "calendars": [{"url": "https://example.com/cal.ics", "name": "Test"}],
        }
        with patch("filter_calendar.requests.get", return_value=self._mock_get(MINIMAL_ICS)):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1
        assert str(events[0].get("SUMMARY")) == "Team Meeting"

    def test_title_exclude_filter(self):
        config = {
            "global_filters": {"title": {"exclude": ["Team"]}},
            "calendars": [{"url": "https://example.com/cal.ics"}],
        }
        with patch("filter_calendar.requests.get", return_value=self._mock_get(MINIMAL_ICS)):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 0

    def test_busy_only_output(self):
        config = {
            "output": {"busy_only": True},
            "global_filters": {},
            "calendars": [{"url": "https://example.com/cal.ics"}],
        }
        with patch("filter_calendar.requests.get", return_value=self._mock_get(MINIMAL_ICS)):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1
        assert str(events[0].get("SUMMARY")) == "Busy"
        assert events[0].get("DESCRIPTION") is None
        assert events[0].get("LOCATION") is None

    def test_multiple_calendars_combined(self):
        config = {
            "calendars": [
                {"url": "https://example.com/cal1.ics"},
                {"url": "https://example.com/cal2.ics"},
            ]
        }
        with patch(
            "filter_calendar.requests.get",
            side_effect=[self._mock_get(MINIMAL_ICS), self._mock_get(ALL_DAY_ICS)],
        ):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 2

    def test_per_calendar_filter_overrides_global(self):
        config = {
            "global_filters": {"title": {"exclude": ["Team"]}},
            "calendars": [
                {
                    "url": "https://example.com/cal.ics",
                    "filters": {"title": {"include": ["Team"]}},
                }
            ],
        }
        with patch("filter_calendar.requests.get", return_value=self._mock_get(MINIMAL_ICS)):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        # Per-calendar filter overrides global: Team is now included
        assert len(events) == 1

    def test_failed_fetch_is_skipped(self):
        config = {
            "calendars": [
                {"url": "https://bad.example.com/cal.ics"},
                {"url": "https://good.example.com/cal.ics"},
            ]
        }
        import requests as req

        bad_response = MagicMock()
        bad_response.raise_for_status.side_effect = req.RequestException("connection error")
        good_response = self._mock_get(MINIMAL_ICS)
        with patch(
            "filter_calendar.requests.get",
            side_effect=[bad_response, good_response],
        ):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1

    def test_all_day_filter_excludes_holiday(self):
        config = {
            "global_filters": {"all_day": False},
            "calendars": [{"url": "https://example.com/cal.ics"}],
        }
        with patch("filter_calendar.requests.get", return_value=self._mock_get(ALL_DAY_ICS)):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 0

    def test_free_busy_filter(self):
        config = {
            "global_filters": {"free_busy": ["FREE"]},
            "calendars": [{"url": "https://example.com/cal.ics"}],
        }
        with patch("filter_calendar.requests.get", return_value=self._mock_get(FREE_ICS)):
            cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1
        assert str(events[0].get("SUMMARY")) == "Out of Office"

    def test_empty_calendars_list(self):
        config = {"calendars": []}
        cal = process_calendars(config)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 0

    def test_output_calendar_metadata(self):
        config = {"calendars": []}
        cal = process_calendars(config)
        assert str(cal.get("VERSION")) == "2.0"
        assert str(cal.get("CALSCALE")) == "GREGORIAN"
