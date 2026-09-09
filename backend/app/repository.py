"""Report persistence. The only module that knows reports live in SQLite."""

import json
import sqlite3
from datetime import datetime, timezone

from .db import Database
from .schemas import Report, ReportSections, ReportSummary, Source


class ReportRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        company: str,
        sections: ReportSections,
        sources: list[Source] | None = None,
    ) -> Report:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = sections.model_dump_json()
        source_payload = json.dumps([s.model_dump() for s in (sources or [])])

        with self.db.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO reports (company, created_at, sections, sources) VALUES (?, ?, ?, ?)",
                (company, created_at, payload, source_payload),
            )
            report_id = int(cursor.lastrowid)

        return Report(
            id=report_id,
            company=company,
            created_at=created_at,
            sections=sections,
            sources=sources or [],
        )

    def list(self) -> list[ReportSummary]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT id, company, created_at FROM reports ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [ReportSummary(**dict(row)) for row in rows]

    def get(self, report_id: int) -> Report | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id, company, created_at, sections, sources FROM reports WHERE id = ?",
                (report_id,),
            ).fetchone()
        return _row_to_report(row) if row else None

    def delete(self, report_id: int) -> bool:
        with self.db.connect() as conn:
            cursor = conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            return cursor.rowcount > 0


def _row_to_report(row: sqlite3.Row) -> Report:
    return Report(
        id=row["id"],
        company=row["company"],
        created_at=row["created_at"],
        sections=ReportSections.model_validate_json(row["sections"]),
        sources=[Source(**s) for s in json.loads(row["sources"])],
    )
