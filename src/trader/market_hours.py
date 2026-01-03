from __future__ import annotations

from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo


def is_market_open(exchange: str, currency: str) -> bool:
    """
    Basic market-hours gate (does not account for holidays).
    Prevents placing orders at weekends / outside regular trading hours.
    """
    now_utc = datetime.now(tz=ZoneInfo("UTC"))

    # UK (LSE): 08:00–16:30 Europe/London
    if currency == "GBP" or exchange == "LSE":
        now_local = now_utc.astimezone(ZoneInfo("Europe/London"))
        if now_local.weekday() >= 5:
            return False
        return dt_time(8, 0) <= now_local.time() <= dt_time(16, 30)

    # US (NYSE/Nasdaq): 09:30–16:00 America/New_York
    now_local = now_utc.astimezone(ZoneInfo("America/New_York"))
    if now_local.weekday() >= 5:
        return False
    return dt_time(9, 30) <= now_local.time() <= dt_time(16, 0)


def is_near_market_close(exchange: str, currency: str, minutes_before_close: int) -> bool:
    """True if we are within N minutes of the market close (regular trading hours)."""
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    mins = int(minutes_before_close)
    if mins <= 0:
        return False

    # UK (LSE): 16:30 Europe/London
    if currency == "GBP" or exchange == "LSE":
        now_local = now_utc.astimezone(ZoneInfo("Europe/London"))
        if now_local.weekday() >= 5:
            return False
        close_time = dt_time(16, 30)
        close_dt = now_local.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
        delta_minutes = (close_dt - now_local).total_seconds() / 60.0
        return 0 <= delta_minutes <= mins

    # US: 16:00 America/New_York
    now_local = now_utc.astimezone(ZoneInfo("America/New_York"))
    if now_local.weekday() >= 5:
        return False
    close_time = dt_time(16, 0)
    close_dt = now_local.replace(hour=close_time.hour, minute=close_time.minute, second=0, microsecond=0)
    delta_minutes = (close_dt - now_local).total_seconds() / 60.0
    return 0 <= delta_minutes <= mins


