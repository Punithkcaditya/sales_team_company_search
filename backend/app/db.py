"""SQLite connection handling and schema.

Reports use a JSON blob for the sections, alongside a daily usage counter. Sections are always read
and written as a whole document, never queried field-by-field, so normalising
them into five tables would buy nothing but joins.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sections   TEXT NOT NULL,
    sources    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_reports_created_at ON reports (created_at DESC);
CREATE TABLE IF NOT EXISTS daily_research_usage (
    day      TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # An in-memory database would be discarded between connections, so keep
        # a single shared one alive for tests.
        self._shared = sqlite3.connect(path, check_same_thread=False) if path == ":memory:" else None
        self.init_schema()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._shared or sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self._shared is None:
                conn.close()
