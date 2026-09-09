"""An app-wide research allowance, separate from provider billing and quotas."""

from datetime import datetime, timedelta, timezone

from .config import Settings
from .db import Database


class DailyUsage:
    def __init__(self, db: Database) -> None:
        self.db = db

    def snapshot(self, settings: Settings, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        day = now.astimezone(timezone.utc).date()
        reset = datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)
        demo = settings.demo_mode
        limit = settings.daily_research_limit or None
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM daily_research_usage WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        used = int(row["attempts"]) if row else 0
        return {
            "mode": "demo" if demo else "live",
            "provider": settings.provider,
            "daily_limit": None if demo else limit,
            "used_today": 0 if demo else used,
            "remaining": max(0, limit - used) if limit and not demo else None,
            "resets_at": reset.isoformat() if not demo and limit else None,
            # Provider usage elsewhere and RPM/TPM/RPD limits are not visible here.
            "provider_tokens_remaining": None,
        }

    def reserve(self, settings: Settings, now: datetime | None = None) -> bool:
        if settings.demo_mode:
            return True
        day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
        limit = settings.daily_research_limit
        # One atomic statement prevents concurrent requests from taking the last
        # allowance twice, including requests handled by separate server workers.
        with self.db.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO daily_research_usage (day, attempts) VALUES (?, 1)
                   ON CONFLICT(day) DO UPDATE SET attempts = attempts + 1
                   WHERE ? = 0 OR attempts < ?""",
                (day, limit, limit),
            )
            return cursor.rowcount == 1
