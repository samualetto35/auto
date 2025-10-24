"""Session calendar logic for the ICT trading agent."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from .config import SessionWindow

NY_TZ = ZoneInfo("America/New_York")
ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
UTC = ZoneInfo("UTC")


def current_session(now: datetime, sessions: list[SessionWindow]) -> SessionWindow | None:
    ny_time = now.astimezone(NY_TZ)
    session_time = ny_time.time()
    for session in sessions:
        if session.contains(session_time):
            return session
    return None


def seconds_until_session_end(now: datetime, session: SessionWindow) -> int:
    ny_time = now.astimezone(NY_TZ)
    end_dt = datetime.combine(ny_time.date(), session.end, tzinfo=NY_TZ)
    if end_dt < ny_time:
        return 0
    return int((end_dt - ny_time).total_seconds())


def is_within_sessions(now: datetime, sessions: list[SessionWindow]) -> bool:
    return current_session(now, sessions) is not None


def convert_to_istanbul(ny_time: datetime) -> datetime:
    return ny_time.astimezone(ISTANBUL_TZ)


def kill_zone_label(session: SessionWindow | None) -> str:
    return session.name if session else "Out of session"
