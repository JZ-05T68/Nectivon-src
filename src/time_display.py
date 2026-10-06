"""Explicit GMT+8 presentation of UTC records, independent of the host timezone."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="GMT+8")


def beijing_now() -> datetime:
    """Return an aware Beijing wall-clock time without a timezone database."""

    return datetime.now(BEIJING_TIMEZONE)


def to_beijing_time(value: datetime | str) -> datetime:
    """Convert an instant; legacy SQLite timestamps without an offset are UTC."""

    parsed = datetime.fromisoformat(value.strip()) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(BEIJING_TIMEZONE)


def format_beijing_time(
    value: datetime | str | None, *, fmt: str = "%Y-%m-%d %H:%M", empty: str = "—",
) -> str:
    """Format recorded times while keeping invalid legacy values visible for review."""

    if value is None or value == "":
        return empty
    try:
        return to_beijing_time(value).strftime(fmt)
    except (ValueError, TypeError, AttributeError):
        # A malformed legacy field is still shown, rather than fabricating a date.
        return str(value)
