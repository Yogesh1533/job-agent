"""SQLite memory of jobs already seen, applied to, or skipped."""
import sqlite3
from datetime import datetime, timezone

STATUSES = ("seen", "applied", "skipped")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    source     TEXT,
    title      TEXT,
    company    TEXT,
    url        TEXT,
    score      INTEGER,
    status     TEXT NOT NULL DEFAULT 'seen',
    first_seen TEXT NOT NULL,
    updated    TEXT NOT NULL
)
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path="jobs.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def known_ids(self):
        return {row["id"] for row in self.conn.execute("SELECT id FROM jobs")}

    def mark_seen(self, job):
        """Record a job as seen. Never downgrades an applied/skipped job."""
        now = _now()
        self.conn.execute(
            """INSERT INTO jobs (id, source, title, company, url, score, status, first_seen, updated)
               VALUES (?, ?, ?, ?, ?, ?, 'seen', ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   score = COALESCE(excluded.score, jobs.score),
                   updated = excluded.updated""",
            (job.id, job.source, job.title, job.company, job.url, job.score, now, now),
        )
        self.conn.commit()

    def set_status(self, job_id, status):
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        cur = self.conn.execute(
            "UPDATE jobs SET status = ?, updated = ? WHERE id = ?", (status, _now(), job_id)
        )
        self.conn.commit()
        return cur.rowcount == 1

    def list(self, status=None):
        sql = "SELECT * FROM jobs"
        args = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY updated DESC"
        return [dict(r) for r in self.conn.execute(sql, args)]
