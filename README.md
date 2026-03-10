# calendar-filter

Generate a custom, public iCalendar (.ics) feed by fetching one or more
existing public calendars (Google, Outlook, or any iCal URL), filtering the
events by configurable criteria, and committing the result back to this
repository.

The feed is updated automatically every day at 5 AM Eastern Time via
GitHub Actions, and can also be triggered manually at any time.

---

## Table of contents

1. [Quick start](#quick-start)
2. [Subscribing to the output feed](#subscribing-to-the-output-feed)
3. [Configuration reference](#configuration-reference)
4. [Running locally](#running-locally)
5. [Running tests](#running-tests)
6. [Unanswered questions and edge cases](#unanswered-questions-and-edge-cases)

---

## Quick start

1. **Fork or clone** this repository.
2. Copy `config.example.yml` to `config.yml`:
   ```bash
   cp config.example.yml config.yml
   ```
3. Edit `config.yml` — add your calendar URLs and desired filters (see
   [Configuration reference](#configuration-reference) below).
4. Commit and push `config.yml`.  The Actions workflow will run automatically
   and write the filtered feed to `output/filtered.ics`.

> **Note:** `config.yml` is tracked by git so your settings persist across
> workflow runs.  Do **not** put private calendar URLs in a public repository.

---

## Subscribing to the output feed

After the workflow runs, the filtered feed is available at the raw GitHub URL:

```
https://raw.githubusercontent.com/<owner>/<repo>/<branch>/output/filtered.ics
```

Paste that URL into any calendar client that supports iCal subscriptions
(Google Calendar → *Other calendars → From URL*, Apple Calendar → *File →
New Calendar Subscription…*, Outlook → *Add calendar → Subscribe from web*).

---

## Configuration reference

All fields are optional unless marked **required**.

```yaml
# ── Output options ────────────────────────────────────────────────────────────
output:
  # busy_only (bool, default: false)
  # When true, every included event is redacted: SUMMARY becomes "Busy" and
  # all properties except DTSTART and DTEND are removed.  Use this to share
  # your availability without disclosing event details.
  busy_only: false

# ── Global filters ────────────────────────────────────────────────────────────
# Applied to every input calendar.  Per-calendar filters (see below) override
# the matching key for that calendar only.
global_filters:

  # title / description / location / creator
  # Each accepts an optional `include` list and/or `exclude` list of
  # case-insensitive regular expressions.
  # - include: event must match at least one pattern to be kept.
  # - exclude: event is dropped if it matches any pattern.
  # Both rules are applied; an event must pass both to be included.
  title:
    include: []     # e.g. ["Team.*", "Sprint \\d+"]
    exclude: []     # e.g. ["Cancelled", "\\[OOO\\]"]

  description:
    exclude: ["confidential"]

  location:
    include: ["Building 2"]

  # creator filters on the ORGANIZER field (email address or display name).
  creator:
    include: ["alice@example.com"]
    exclude: ["noreply@"]

  # free_busy: list of allowed transparency statuses.
  #   "BUSY"  → TRANSP:OPAQUE  (blocks calendar time — the iCal default)
  #   "FREE"  → TRANSP:TRANSPARENT  (does not block time)
  # Omit (or list both) to include events regardless of transparency.
  free_busy:
    - "BUSY"

  # all_day (bool or null, default: null)
  #   true  → include only all-day events
  #   false → include only timed (non-all-day) events
  #   null  → include both
  all_day: false

  # start_time / end_time: filter on DTSTART / DTEND.
  #   after:  keep events whose time is ≥ this ISO 8601 value.
  #   before: keep events whose time is <  this ISO 8601 value.
  # Bare dates (no time component) are treated as midnight UTC.
  start_time:
    after: "2025-01-01T00:00:00Z"
  end_time:
    before: "2027-01-01T00:00:00Z"

# ── Input calendars ───────────────────────────────────────────────────────────
calendars:
  - url: "https://calendar.google.com/calendar/ical/REPLACE_ME/basic.ics"  # required
    name: "My Google Calendar"     # optional label used in log output
    filters:                       # optional per-calendar overrides
      title:
        exclude: ["Private"]

  - url: "https://outlook.live.com/owa/calendar/REPLACE_ME/reachcalendar.ics"
    name: "My Outlook Calendar"
```

### Filter merging

When both `global_filters` and a per-calendar `filters` block specify the same
key (e.g. `title`), the **per-calendar value replaces** the global value for
that calendar.  Keys that appear only in `global_filters` continue to apply.

---

## Running locally

```bash
# 1. Install dependencies
pip install -r src/requirements.txt

# 2. Run the filter (reads config.yml, writes output/filtered.ics)
python src/filter_calendar.py config.yml output/filtered.ics
```

---

## Running tests

```bash
pip install -r src/requirements.txt pytest
pytest tests/ -v
```

---

## Unanswered questions and edge cases

The following questions and edge cases should be resolved to improve the
solution before relying on it for production use:

### Recurring events
- The current implementation filters on the **master VEVENT** (the rule
  definition) rather than on individual occurrences.  A recurring event whose
  `DTSTART` is in the past will still be included even if no future occurrences
  fall within a configured `start_time.after` window.
- Expanding recurrences (RRULE / RDATE / EXDATE) would require a library such
  as `python-recurring-ical-events` and significantly increases complexity.
  Should we expand before filtering?

### Timezone handling
- All-day events have no timezone; they are normalised to midnight UTC.  This
  may cause off-by-one errors near day boundaries for users in non-UTC zones.
- Should `start_time` / `end_time` filters compare in the event's local
  timezone or always in UTC?

### Daylight saving time
- The cron schedule is fixed at `0 10 * * *` (10:00 UTC = 5:00 AM EST).
  During EDT (UTC-4) the workflow runs at 6:00 AM ET instead of 5:00 AM.
  GitHub Actions does not support timezone-aware schedules natively.  A
  workaround would be a two-line cron (`0 9 * * *` and `0 10 * * *`) with
  conditional logic, or using a separate service to trigger the workflow.

### Authentication / private calendars
- Only public (unauthenticated) iCal URLs are currently supported.  Should
  Basic Auth, OAuth tokens, or other credentials be configurable per-calendar?

### Error handling and resilience
- A failed calendar fetch logs an error and continues.  Should a single
  failure abort the entire run, or should partial results still be published?
- What is the desired behaviour when `config.yml` is missing or malformed?

### Output feed size and pagination
- There is no upper limit on the number of events in the output.  Very large
  calendars could create oversized `.ics` files.  Should a maximum number of
  events or a rolling time window (e.g. next 90 days) be enforced?

### ATTENDEE vs ORGANIZER for "creator"
- The `creator` filter matches against the `ORGANIZER` field.  Some calendar
  systems store the creator differently (e.g. only in `ATTENDEE` with the
  `ROLE=CHAIR` parameter).  Should `ATTENDEE` also be matched?

### Events without DTEND
- Some events use `DURATION` instead of `DTEND`.  The `end_time` filter will
  silently pass these events through.  Should `DURATION` be resolved to
  `DTEND` for filtering and output purposes?

### Character encoding
- iCal feeds may be encoded in Latin-1 or other charsets.  The current
  implementation decodes using the `icalendar` library's defaults.  Malformed
  or non-UTF-8 feeds may raise exceptions.

### iCal spec compliance of the output feed
- When `busy_only` is `false`, calendar components other than `VEVENT`
  (e.g. `VTIMEZONE`, `VALARM`) are not copied to the output.  Some calendar
  clients require `VTIMEZONE` components for correct timezone rendering.

### Configuration secrets
- Calendar URLs for private feeds should not be committed to a public
  repository.  A future enhancement could read URLs from GitHub Actions
  Secrets and inject them at runtime.

### Cache / conditional HTTP requests
- The workflow fetches each calendar URL unconditionally on every run.  Using
  `ETag` / `Last-Modified` headers could reduce network usage and avoid
  committing unchanged output.
