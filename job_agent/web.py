"""Local web dashboard for the job agent.

    python3 -m job_agent.web          # opens http://127.0.0.1:8765

Runs only on your own computer (bound to 127.0.0.1), costs nothing, and reads/writes
the same jobs.db as the command line.
"""
import argparse
import os
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, send_from_directory

from job_agent import classify, llm, writer
from job_agent import main as cli
from job_agent import sources as sources_mod
from job_agent.store import STATUSES, JobStore

STATIC_DIR = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost"}

DEFAULT_SETTINGS = {
    "keywords": "it support,helpdesk,cyber,ai",
    "location": "Sydney",
    "resume": "resume.txt",
    "sources": "",
    "part_time": True,
    "fits_only": False,
    "term_break": False,
    "show_blocked": False,
    "limit": 20,
    "max_score": 15,
    "draft_top": 5,
    "work_modes": [],   # empty = any
    "pays": [],
    "job_types": [],
    "schedule_enabled": False,
    "schedule_time": "08:00",
    "schedule_days": [0, 1, 2, 3, 4],  # Mon-Fri (Python: Monday is 0)
}
TEXT_FIELDS = {"keywords": 300, "location": 100, "resume": 200, "sources": 100}
BOOL_FIELDS = ("part_time", "fits_only", "term_break", "show_blocked", "schedule_enabled")
INT_FIELDS = {"limit": (1, 100), "max_score": (0, 100), "draft_top": (0, 20)}
LIST_FIELDS = {"work_modes": classify.WORK_MODES, "pays": classify.PAY_OPTIONS, "job_types": classify.JOB_TYPES}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SettingsError(ValueError):
    pass


def validate_settings(data, current):
    """Merge and check user-submitted settings. Raises SettingsError."""
    if not isinstance(data, dict):
        raise SettingsError("Settings must be a JSON object.")
    out = dict(current)
    for key, value in data.items():
        if key in TEXT_FIELDS:
            if not isinstance(value, str) or len(value) > TEXT_FIELDS[key]:
                raise SettingsError(f"{key} must be text up to {TEXT_FIELDS[key]} characters.")
            out[key] = value.strip()
        elif key in BOOL_FIELDS:
            if not isinstance(value, bool):
                raise SettingsError(f"{key} must be true or false.")
            out[key] = value
        elif key in INT_FIELDS:
            low, high = INT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise SettingsError(f"{key} must be a whole number from {low} to {high}.")
            out[key] = value
        elif key in LIST_FIELDS:
            allowed = LIST_FIELDS[key]
            if not isinstance(value, list) or any(v not in allowed for v in value):
                raise SettingsError(f"{key} must be a list using: {', '.join(allowed)}.")
            out[key] = [v for v in allowed if v in value]
        elif key == "schedule_time":
            if not isinstance(value, str) or not TIME_RE.match(value):
                raise SettingsError("schedule_time must look like 08:00.")
            out[key] = value
        elif key == "schedule_days":
            if (not isinstance(value, list) or not value
                    or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in value)):
                raise SettingsError("schedule_days must list at least one day (0=Mon ... 6=Sun).")
            out[key] = sorted(set(value))
        else:
            raise SettingsError(f"Unknown setting: {key}")
    if out["resume"]:
        safe_resume_path(out["resume"])
    if out["sources"]:
        unknown = [s for s in (x.strip() for x in out["sources"].split(",")) if s and s not in sources_mod.SOURCES]
        if unknown:
            raise SettingsError(f"Unknown source(s): {', '.join(unknown)}")
    return out


def safe_resume_path(path):
    """The resume is sent to the AI, so only allow a .txt file inside this project folder."""
    base = Path.cwd().resolve()
    full = (base / path).resolve()
    if full.suffix.lower() != ".txt" or (full != base and base not in full.parents):
        raise SettingsError("Resume must be a .txt file inside the job-agent folder.")
    return full


def settings_to_argv(s, db):
    argv = ["--db", db, "--limit", str(s["limit"]), "--max-score", str(s["max_score"]),
            "--draft-top", str(s["draft_top"])]
    for key, flag in (("keywords", "--keywords"), ("location", "--location"),
                      ("sources", "--sources"), ("resume", "--resume")):
        if s.get(key):
            argv += [flag, s[key]]
    for key, flag in (("work_modes", "--work-mode"), ("pays", "--pay"), ("job_types", "--job-type")):
        chosen = s.get(key) or []
        if chosen and set(chosen) != set(LIST_FIELDS[key]):
            argv += [flag, ",".join(chosen)]
    for key, flag in (("part_time", "--part-time"), ("fits_only", "--fits-only"),
                      ("term_break", "--term-break"), ("show_blocked", "--show-blocked")):
        if s.get(key):
            argv.append(flag)
    return argv


def next_run_time(settings, now, last_date=None):
    """When the scheduled search will next run, or None if scheduling is off.
    If today's time has passed and it hasn't run today, it's due now (catch-up)."""
    if not settings.get("schedule_enabled"):
        return None
    hh, mm = (int(x) for x in settings["schedule_time"].split(":"))
    days = set(settings.get("schedule_days") or [])
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() not in days or day.isoformat() == last_date:
            continue
        return datetime.combine(day, dtime(hh, mm))
    return None


class Runner:
    """Runs one search at a time in a background thread."""

    def __init__(self, db):
        self.db = db
        self._lock = threading.Lock()
        self.thread = None

    @property
    def running(self):
        return self._lock.locked()

    def start(self, trigger="manual"):
        if not self._lock.acquire(blocking=False):
            return False
        self.thread = threading.Thread(target=self._run, args=(trigger,), daemon=True)
        self.thread.start()
        return True

    def _run(self, trigger):
        try:
            store = JobStore(self.db)
            try:
                settings = store.get_settings(DEFAULT_SETTINGS)
                args = cli.parse_args(settings_to_argv(settings, self.db))
                os.environ["COMPANIES_FILE"] = args.companies
                cli.run_search(args, store, trigger=trigger, write_output=False)
            except Exception:
                pass  # already recorded in the run's history
            finally:
                store.close()
        finally:
            self._lock.release()


class Scheduler(threading.Thread):
    def __init__(self, db, runner, interval=30):
        super().__init__(daemon=True)
        self.db, self.runner, self.interval = db, runner, interval

    def tick(self, now=None):
        now = now or datetime.now()
        store = JobStore(self.db)
        try:
            settings = store.get_settings(DEFAULT_SETTINGS)
            due = next_run_time(settings, now, store.get_value("last_scheduled_date"))
            if due and due <= now and not self.runner.running:
                store.set_value("last_scheduled_date", now.date().isoformat())
                return self.runner.start("schedule")
            return False
        finally:
            store.close()

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:  # never let the scheduler thread die
                cli.warn(f"Scheduler error: {e}")
            time.sleep(self.interval)


def setup_status():
    """What's configured. Never returns key values."""
    companies = 0
    try:
        from job_agent.ats import load_companies
        companies = len(load_companies(sources_mod.companies_file()))
    except (OSError, ValueError):
        pass
    try:
        provider = llm.provider()
    except llm.LLMError:
        provider = None
    return {
        "ai_provider": provider,
        "ai_model": llm.get_model() if provider else None,
        "ai_problem": llm.config_problem(),
        "adzuna": sources_mod.adzuna_configured(),
        "jooble": sources_mod.jooble_configured(),
        "companies": companies,
        "default_sources": sources_mod.default_sources(),
        "committed_hours": float(os.getenv("COMMITTED_FORTNIGHT_HOURS", "0") or 0),
        "visa_cap": float(os.getenv("VISA_FORTNIGHT_CAP", "48") or 48),
    }


def create_app(db="jobs.db", start_scheduler=True):
    app = Flask(__name__, static_folder=None)
    app.json.sort_keys = False
    runner = Runner(db)
    app.config["RUNNER"] = runner

    store = JobStore(db)
    store.fail_stale_runs()
    store.close()

    if start_scheduler:
        Scheduler(db, runner).start()

    def open_store():
        return JobStore(db)

    # --- security: this app is for your computer only ---------------------
    @app.before_request
    def local_only():
        if (request.host.rsplit(":", 1)[0] if request.host else "") not in LOCAL_HOSTS:
            abort(403)  # blocks DNS-rebinding tricks from other websites
        if request.method in ("POST", "PUT", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
                abort(403)  # another site trying to drive this app
            if not request.is_json:
                abort(415)  # plain HTML forms from other sites can't send JSON

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(SettingsError)
    def bad_settings(e):
        return jsonify(error=str(e)), 400

    # --- pages ------------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name):
        return send_from_directory(STATIC_DIR, name)

    # --- API --------------------------------------------------------------
    @app.get("/api/summary")
    def summary():
        s = open_store()
        try:
            settings = s.get_settings(DEFAULT_SETTINGS)
            nxt = next_run_time(settings, datetime.now(), s.get_value("last_scheduled_date"))
            runs = s.list_runs(limit=1)
            return jsonify(
                counts=s.job_counts(),
                running=runner.running,
                current_run=runs[0] if runs and runs[0]["status"] == "running" else None,
                last_run=s.last_run(),
                next_run=nxt.isoformat(timespec="minutes") if nxt else None,
                top_jobs=s.search_jobs(status="seen", sort="score", limit=5),
                setup=setup_status(),
                resume_found=bool(settings["resume"]) and os.path.exists(settings["resume"]),
            )
        finally:
            s.close()

    @app.get("/api/jobs")
    def jobs():
        a = request.args
        status = a.get("status") or None
        if status and status not in STATUSES:
            return jsonify(error="Unknown status"), 400
        try:
            min_score = int(a["min_score"]) if a.get("min_score") else None
            run_id = int(a["run_id"]) if a.get("run_id") else None
        except ValueError:
            return jsonify(error="min_score and run_id must be numbers"), 400
        s = open_store()
        try:
            return jsonify(jobs=s.search_jobs(
                status=status, q=(a.get("q") or "").strip()[:100] or None, min_score=min_score,
                verdict=a.get("verdict") or None, run_id=run_id, sort=a.get("sort", "score"),
                work_mode=a.get("work_mode") or None, pay=a.get("pay") or None,
                job_type=a.get("job_type") or None))
        finally:
            s.close()

    @app.get("/api/job")
    def job():
        s = open_store()
        try:
            found = s.get_job(request.args.get("id", ""))
        finally:
            s.close()
        return (jsonify(job=found), 200) if found else (jsonify(error="Job not found"), 404)

    @app.post("/api/job/status")
    def job_status():
        data = request.get_json(silent=True) or {}
        if data.get("status") not in STATUSES:
            return jsonify(error=f"status must be one of {', '.join(STATUSES)}"), 400
        s = open_store()
        try:
            ok = s.set_status(str(data.get("id", "")), data["status"])
        finally:
            s.close()
        return (jsonify(ok=True), 200) if ok else (jsonify(error="Job not found"), 404)

    @app.post("/api/job/write")
    def job_write():
        """Write (or rewrite) the cover letter or tailored resume for one job."""
        data = request.get_json(silent=True) or {}
        kind = data.get("kind")
        if kind not in ("cover_letter", "resume"):
            return jsonify(error="kind must be cover_letter or resume"), 400
        problem = llm.config_problem()
        if problem:
            return jsonify(error=problem), 400
        s = open_store()
        try:
            found = s.get_job(str(data.get("id", "")))
            if not found:
                return jsonify(error="Job not found"), 404
            settings = s.get_settings(DEFAULT_SETTINGS)
            if not settings["resume"]:
                return jsonify(error="Set your resume file in Settings first."), 400
            path = safe_resume_path(settings["resume"])
            if not path.exists():
                return jsonify(error=f"Resume file not found: {settings['resume']}"), 400
            resume_text = path.read_text(encoding="utf-8", errors="replace")
            try:
                if kind == "cover_letter":
                    s.save_documents(found["id"], cover_letter=writer.cover_letter(found, resume_text))
                else:
                    text, warnings = writer.tailored_resume(found, resume_text)
                    s.save_documents(found["id"], tailored_resume=text, warnings=warnings)
            except (llm.LLMError, writer.WriterError) as e:
                return jsonify(error=str(e)), 502
            return jsonify(job=s.get_job(found["id"]))
        finally:
            s.close()

    @app.get("/api/runs")
    def runs():
        s = open_store()
        try:
            return jsonify(runs=s.list_runs(limit=100))
        finally:
            s.close()

    @app.get("/api/runs/<int:run_id>")
    def run_detail(run_id):
        s = open_store()
        try:
            found = s.get_run(run_id)
        finally:
            s.close()
        return (jsonify(run=found), 200) if found else (jsonify(error="Run not found"), 404)

    @app.post("/api/runs")
    def start_run():
        s = open_store()
        try:
            settings = s.get_settings(DEFAULT_SETTINGS)
        finally:
            s.close()
        if settings["resume"]:
            safe_resume_path(settings["resume"])
        if not runner.start("manual"):
            return jsonify(error="A search is already running."), 409
        return jsonify(started=True), 202

    @app.get("/api/settings")
    def get_settings():
        s = open_store()
        try:
            return jsonify(settings=s.get_settings(DEFAULT_SETTINGS))
        finally:
            s.close()

    @app.put("/api/settings")
    def put_settings():
        s = open_store()
        try:
            new = validate_settings(request.get_json(silent=True), s.get_settings(DEFAULT_SETTINGS))
            s.save_settings(new)
            return jsonify(settings=new)
        finally:
            s.close()

    return app


def main(argv=None):
    p = argparse.ArgumentParser(description="Job agent dashboard (local only).")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--db", default="jobs.db")
    p.add_argument("--companies", default="companies.json")
    p.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically")
    args = p.parse_args(argv)
    load_dotenv()
    os.environ["COMPANIES_FILE"] = args.companies
    app = create_app(args.db)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Job agent dashboard: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
