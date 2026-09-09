"""Bounded 2026 US equity schedule, checked against NYSE and Nasdaq 2026-09-08.

Scheduled hours only: unexpected closures and instrument halts remain unknown.
No inference is made for unsupported calendar years or non-US instruments.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
            "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
            "2026-11-26", "2026-12-25"}
EARLY = {"2026-11-27", "2026-12-24"}
SOURCES = ["https://www.nyse.com/trade/hours-calendars",
           "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule"]


def scheduled_session(date):
    if date.year != 2026:
        return None
    if date.weekday() >= 5 or date.isoformat() in HOLIDAYS:
        return {}
    opened = datetime(date.year, date.month, date.day, 9, 30, tzinfo=NY)
    closed = opened.replace(hour=13 if date.isoformat() in EARLY else 16, minute=0)
    return {"openAt": opened.astimezone(timezone.utc).isoformat(),
            "closeAt": closed.astimezone(timezone.utc).isoformat(),
            "date": date.isoformat(), "earlyClose": date.isoformat() in EARLY}


def session_status(now):
    local = now.astimezone(NY)
    today = scheduled_session(local.date())
    last = None
    for i in range(15):
        candidate = scheduled_session(local.date() - timedelta(days=i))
        if candidate and datetime.fromisoformat(candidate["closeAt"]) <= now:
            last = candidate["date"]
            break
    opened = bool(today and datetime.fromisoformat(today["openAt"]) <= now < datetime.fromisoformat(today["closeAt"]))
    return {"status": "SCHEDULED_OPEN" if opened else "CALENDAR_UNKNOWN" if today is None else "SCHEDULED_CLOSED",
            "asOf": now.isoformat(), "timezone": "America/New_York", "date": local.date().isoformat(),
            "scheduleKnown": today is not None, "scheduledOpen": opened,
            "session": today, "lastCompletedSession": last,
            "calendar": "NYSE/Nasdaq US equities 2026 published schedule; unscheduled closures and halts unverified",
            "verifiedAt": "2026-09-08", "sources": SOURCES, "haltsVerified": False,
            "recovery": "Check instrument halts and current event risk separately; other calendar years remain unknown."}
