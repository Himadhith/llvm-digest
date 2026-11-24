"""SQLite-backed state: remembers which items were posted or already evaluated."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    key        TEXT PRIMARY KEY,
    status     TEXT NOT NULL CHECK (status IN ('posted', 'skipped')),
    title      TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    """Tracks item keys such as ``discourse:92805`` / ``github:244580``.

    * ``posted``  - a summary was delivered; never alert again
    * ``skipped`` - fetched and judged irrelevant; avoids re-downloading the body next run
    """

    def __init__(self, path: str) -> None:
        self._path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(Path(path).expanduser()) if path != ":memory:" else path)
        self._conn.execute("PRAGMA journal_mode=WAL" if path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def is_known(self, key: str) -> bool:
        """True if the key was already posted or skipped."""
        row = self._conn.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone()
        return row is not None

    def status(self, key: str) -> str | None:
        """Return ``posted`` / ``skipped`` / ``None``."""
        row = self._conn.execute("SELECT status FROM seen WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def mark(self, key: str, status: str, title: str = "", url: str = "") -> None:
        """Record ``key``. Posted never gets downgraded to skipped."""
        self._conn.execute(
            """
            INSERT INTO seen (key, status, title, url, updated_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                status = CASE WHEN seen.status = 'posted' THEN 'posted' ELSE excluded.status END,
                title = CASE WHEN excluded.title = '' THEN seen.title ELSE excluded.title END,
                url = CASE WHEN excluded.url = '' THEN seen.url ELSE excluded.url END,
                updated_at = excluded.updated_at
            """,
            (key, status, title, url, _now()),
        )
        self._conn.commit()

    def mark_posted(self, keys: Iterable[str]) -> int:
        """Mark many keys as posted; returns how many were given."""
        count = 0
        for key in keys:
            self.mark(key, "posted")
            count += 1
        return count

    def counts(self) -> dict[str, int]:
        """Row counts per status."""
        rows = self._conn.execute("SELECT status, COUNT(*) FROM seen GROUP BY status").fetchall()
        return {str(status): int(n) for status, n in rows}

    def prune(self, older_than_days: int = 180) -> int:
        """Delete old ``skipped`` rows so the DB stays small. Posted rows are kept."""
        cutoff = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
        cur = self._conn.execute("DELETE FROM seen WHERE status = 'skipped' AND updated_at < ?", (cutoff_iso,))
        self._conn.commit()
        return cur.rowcount
