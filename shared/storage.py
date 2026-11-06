"""SQLite persistence for generated daily reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ReportStorage:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_date TEXT NOT NULL UNIQUE,
                    team TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    html TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save_report(
        self,
        report_date: str,
        team: str,
        markdown: str,
        html: str,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO daily_reports (report_date, team, markdown, html)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(report_date) DO UPDATE SET
                    team = excluded.team,
                    markdown = excluded.markdown,
                    html = excluded.html
                """,
                (report_date, team, markdown, html),
            )
        if cursor.lastrowid is None or cursor.lastrowid == 0:
            row = self.get_report(report_date)
            if row is None:
                raise RuntimeError("Failed to persist report")
            return int(row["id"])
        return int(cursor.lastrowid)

    def get_report(self, report_date: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM daily_reports WHERE report_date = ?",
                (report_date,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_reports(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM daily_reports
                ORDER BY report_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
