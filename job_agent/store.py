"""SQLite storage: every job found (with its score, cover note and checks),
every search run, and the dashboard's settings."""
import json
import sqlite3
from datetime import datetime, timezone

from job_agent.utils import strip_html

STATUSES = ("seen", "applied", "skipped")

JOBS_SCHEMA = """
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

# Columns added after the first version. Old databases get them on open.
JOB_EXTRA_COLUMNS = {
    "location": "TEXT",
    "employment_type": "TEXT",
    "description": "TEXT",
    "reasons": "TEXT",          # JSON list
    "missing_skills": "TEXT",   # JSON list
    "verdict": "TEXT",
    "verdict_note": "TEXT",
    "positives": "TEXT",        # JSON list
    "draft": "TEXT",
    "draft_error": "TEXT",
    "run_id": "INTEGER",
    "last_seen": "TEXT",
    "work_mode": "TEXT",
    "pay": "TEXT",
    "salary_text": "TEXT",
    "category": "TEXT",
    "tailored_resume": "TEXT",
    "tailored_warnings": "TEXT",  # JSON list
    "docs_updated": "TEXT",
}

RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started  TEXT NOT NULL,
    finished TEXT,
    status   TEXT NOT NULL DEFAULT 'running',
    trigger  TEXT,
    params   TEXT,
    fetched  INTEGER,
    matched  INTEGER,
    new      INTEGER,
    scored   INTEGER,
    drafted  INTEGER,
    shown    INTEGER,
    log      TEXT,
    error    TEXT
)
"""

SETTINGS_SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

JSON_COLUMNS = ("reasons", "missing_skills", "positives", "tailored_warnings")
RUN_STATS = ("fetched", "matched", "new", "scored", "drafted", "shown")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_list(values):
    return json.dumps(list(values)) if values else None


def _decode(row):
    if row is None:
        return None
    d = dict(row)
    for col in JSON_COLUMNS:
        if col in d:
            try:
                d[col] = json.loads(d[col]) if d[col] else []
            except ValueError:
                d[col] = []
    if "params" in d:
        try:
            d["params"] = json.loads(d["params"]) if d["params"] else {}
        except ValueError:
            d["params"] = {}
    return d


class JobStore:
    def __init__(self, path="jobs.db"):
        self.conn = sqlite3.connect(path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")  # dashboard reads while a run writes
        except sqlite3.DatabaseError:
            pass
        self.conn.execute(JOBS_SCHEMA)
        self.conn.execute(RUNS_SCHEMA)
        self.conn.execute(SETTINGS_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        for name, kind in JOB_EXTRA_COLUMNS.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {kind}")

    def close(self):
        self.conn.close()

    # --- jobs -----------------------------------------------------------

    def known_ids(self):
        return {row["id"] for row in self.conn.execute("SELECT id FROM jobs")}

    def save_job(self, job, run_id=None, draft=None, draft_error=None):
        """Store a job and everything worked out about it. Never downgrades applied/skipped,
        and never wipes an earlier score or cover note with an empty one."""
        now = _now()
        c = getattr(job, "student", None)
        scored = job.score is not None
        employment_type = job.employment_type or (c.employment_type if c else "") or None
        description = strip_html(job.description)[:4000] or None
        self.conn.execute(
            """INSERT INTO jobs (id, source, title, company, url, score, status, first_seen, updated,
                                 location, employment_type, description, reasons, missing_skills,
                                 verdict, verdict_note, positives, draft, draft_error, run_id, last_seen,
                                 work_mode, pay, salary_text, category, docs_updated)
               VALUES (?, ?, ?, ?, ?, ?, 'seen', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   title = excluded.title,
                   company = excluded.company,
                   url = excluded.url,
                   location = excluded.location,
                   employment_type = COALESCE(excluded.employment_type, jobs.employment_type),
                   description = COALESCE(excluded.description, jobs.description),
                   score = COALESCE(excluded.score, jobs.score),
                   reasons = COALESCE(excluded.reasons, jobs.reasons),
                   missing_skills = COALESCE(excluded.missing_skills, jobs.missing_skills),
                   verdict = COALESCE(excluded.verdict, jobs.verdict),
                   verdict_note = COALESCE(excluded.verdict_note, jobs.verdict_note),
                   positives = COALESCE(excluded.positives, jobs.positives),
                   draft = COALESCE(excluded.draft, jobs.draft),
                   draft_error = CASE WHEN excluded.draft IS NOT NULL THEN NULL
                                      ELSE COALESCE(excluded.draft_error, jobs.draft_error) END,
                   run_id = COALESCE(excluded.run_id, jobs.run_id),
                   work_mode = COALESCE(excluded.work_mode, jobs.work_mode),
                   pay = COALESCE(excluded.pay, jobs.pay),
                   salary_text = COALESCE(excluded.salary_text, jobs.salary_text),
                   category = COALESCE(excluded.category, jobs.category),
                   docs_updated = COALESCE(excluded.docs_updated, jobs.docs_updated),
                   last_seen = excluded.last_seen,
                   updated = excluded.updated""",
            (
                job.id, job.source, job.title, job.company, job.url, job.score, now, now,
                job.location or None, employment_type, description,
                _json_list(job.reasons) if scored else None,
                _json_list(job.missing_skills) if scored else None,
                c.verdict if c else None, c.note if c else None,
                _json_list(c.positives) if c else None,
                draft, draft_error, run_id, now,
                job.work_mode or None, job.pay or None, job.salary_text or None, job.category or None,
                now if draft else None,
            ),
        )
        self.conn.commit()

    mark_seen = save_job  # older name

    def save_documents(self, job_id, cover_letter=None, tailored_resume=None, warnings=None):
        """Store a (re)written cover letter and/or tailored resume for a job."""
        sets, args = ["docs_updated = ?", "updated = ?"], [_now(), _now()]
        if cover_letter is not None:
            sets += ["draft = ?", "draft_error = NULL"]
            args.append(cover_letter)
        if tailored_resume is not None:
            sets += ["tailored_resume = ?", "tailored_warnings = ?"]
            args += [tailored_resume, json.dumps(list(warnings or []))]
        args.append(job_id)
        cur = self.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
        self.conn.commit()
        return cur.rowcount == 1

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
        return [_decode(r) for r in self.conn.execute(sql, args)]

    def get_job(self, job_id):
        return _decode(self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def search_jobs(self, status=None, q=None, min_score=None, verdict=None, run_id=None,
                    sort="score", limit=500, work_mode=None, pay=None, job_type=None):
        where, args = [], []
        for column, value in (("work_mode", work_mode), ("pay", pay), ("employment_type", job_type)):
            if value == "unknown":
                where.append(f"({column} IS NULL OR {column} = '')")
            elif value:
                where.append(f"{column} = ?")
                args.append(value)
        if status:
            where.append("status = ?")
            args.append(status)
        if verdict:
            where.append("verdict = ?")
            args.append(verdict)
        if min_score is not None:
            where.append("score >= ?")
            args.append(int(min_score))
        if run_id is not None:
            where.append("run_id = ?")
            args.append(int(run_id))
        if q:
            where.append("(title LIKE ? OR company LIKE ? OR location LIKE ?)")
            like = f"%{q}%"
            args += [like, like, like]
        order = {
            "score": "score IS NULL, score DESC, first_seen DESC",
            "recent": "first_seen DESC",
            "updated": "updated DESC",
        }.get(sort, "score IS NULL, score DESC, first_seen DESC")
        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order} LIMIT ?"
        args.append(int(limit))
        rows = [_decode(r) for r in self.conn.execute(sql, args)]
        for r in rows:
            r.pop("description", None)  # list view doesn't need it
            r["has_draft"] = bool(r.pop("draft", None))
            r["has_resume"] = bool(r.pop("tailored_resume", None))
            r.pop("tailored_warnings", None)
        return rows

    def job_counts(self):
        counts = {s: 0 for s in STATUSES}
        for r in self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
            counts[r["status"]] = r["n"]
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, AVG(score) AS avg_score, "
            "SUM(CASE WHEN score >= 70 THEN 1 ELSE 0 END) AS strong, "
            "SUM(CASE WHEN draft IS NOT NULL THEN 1 ELSE 0 END) AS drafts "
            "FROM jobs"
        ).fetchone()
        return {
            "by_status": counts,
            "total": row["total"] or 0,
            "avg_score": round(row["avg_score"]) if row["avg_score"] is not None else None,
            "strong": row["strong"] or 0,
            "drafts": row["drafts"] or 0,
        }

    # --- runs -----------------------------------------------------------

    def start_run(self, trigger, params):
        cur = self.conn.execute(
            "INSERT INTO runs (started, status, trigger, params) VALUES (?, 'running', ?, ?)",
            (_now(), trigger, json.dumps(params)),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id, status, stats=None, log_lines=None, error=None):
        stats = stats or {}
        self.conn.execute(
            f"""UPDATE runs SET finished = ?, status = ?, log = ?, error = ?,
                {", ".join(f"{k} = ?" for k in RUN_STATS)} WHERE id = ?""",
            (_now(), status, "\n".join(log_lines or []), error,
             *[stats.get(k) for k in RUN_STATS], run_id),
        )
        self.conn.commit()

    def list_runs(self, limit=50):
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        out = []
        for r in rows:
            d = _decode(r)
            d.pop("log", None)
            out.append(d)
        return out

    def get_run(self, run_id):
        return _decode(self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())

    def last_run(self):
        return _decode(self.conn.execute(
            "SELECT * FROM runs WHERE status != 'running' ORDER BY id DESC LIMIT 1").fetchone())

    def fail_stale_runs(self):
        """Runs still marked 'running' when the app starts were interrupted."""
        self.conn.execute(
            "UPDATE runs SET status = 'failed', finished = ?, error = 'Interrupted (app was closed)' "
            "WHERE status = 'running'", (_now(),))
        self.conn.commit()

    # --- settings -------------------------------------------------------

    def get_value(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_value(self, key, value):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        self.conn.commit()

    def get_settings(self, defaults):
        saved = self.get_value("settings", {}) or {}
        return {**defaults, **{k: v for k, v in saved.items() if k in defaults}}

    def save_settings(self, settings):
        self.set_value("settings", settings)
