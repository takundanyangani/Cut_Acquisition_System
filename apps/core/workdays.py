"""Working-day arithmetic. Weekends and dates in the Holiday table are skipped."""

from datetime import datetime, timedelta

from django.utils import timezone


def _holidays_between(start, end):
    from .models import Holiday

    return set(Holiday.objects.filter(date__gte=start, date__lte=end).values_list("date", flat=True))


def is_working_day(day, holidays=None):
    if day.weekday() >= 5:
        return False
    if holidays is None:
        holidays = _holidays_between(day, day)
    return day not in holidays


def add_working_days(start, days):
    """Return `start` moved forward by `days` working days.

    Accepts a date or an aware datetime and returns the same type. A datetime
    keeps its time of day, so a request that reaches a desk at 15:00 on a
    Friday with a one-day target is due at 15:00 on Monday.
    """
    if days <= 0:
        return start
    as_datetime = isinstance(start, datetime)
    local = timezone.localtime(start) if as_datetime else None
    day = local.date() if as_datetime else start
    # Look far enough ahead to cover long holiday runs.
    holidays = _holidays_between(day, day + timedelta(days=days * 3 + 21))
    remaining = days
    while remaining:
        day += timedelta(days=1)
        if is_working_day(day, holidays):
            remaining -= 1
    if as_datetime:
        return local.replace(year=day.year, month=day.month, day=day.day)
    return day


def working_days_between(start, end):
    """Whole working days from `start` to `end`, not counting the start day."""
    if isinstance(start, datetime):
        start = timezone.localtime(start).date()
    if isinstance(end, datetime):
        end = timezone.localtime(end).date()
    if end <= start:
        return 0
    holidays = _holidays_between(start, end)
    count = 0
    day = start
    while day < end:
        day += timedelta(days=1)
        if is_working_day(day, holidays):
            count += 1
    return count


def today():
    return timezone.localdate()
