import json
import sqlite3
from datetime import datetime

import pytest

from job_agent import main as cli
from job_agent import web
from job_agent.models import Job
from job_agent.store import JobStore
from job_agent.student import StudentCheck

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resume.txt").write_text("IT support, Microsoft 365")
    monkeypatch.setenv("COMPANIES_FILE", str(tmp_path / "none.json"))
    for var in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return web.create_app(str(tmp_path / "jobs.db"), start_scheduler=False)


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, path, body, method="post", **headers):
    return getattr(client, method)(path, data=json.dumps(body), content_type="application/json",
                                   base_url=BASE, headers=headers)


def get(client, path):
    return client.get(path, base_url=BASE)


def job(i, **kw):
    j = Job(id=f"adzuna:{i}", title=f"Helpdesk {i}", company="Acme", location="Sydney NSW",
            url=f"https://example.com/{i}", description="<p>Casual, 8 hours per week</p>", tags=[], source="adzuna")
    for k, v in kw.items():
        setattr(j, k, v)
    return j


def seed(app, *jobs, run_id=None, draft=None):
    store = JobStore(app.config["RUNNER"].db)
    for j in jobs:
        store.save_job(j, run_id=run_id, draft=draft)
    store.close()


# --- pages and security -------------------------------------------------

def test_index_and_static_served_with_security_headers(client):
    r = get(client, "/")
    assert r.status_code == 200 and b"Job Agent" in r.data
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert get(client, "/static/app.js").status_code == 200
    assert get(client, "/static/../web.py").status_code == 404


def test_rejects_other_hosts(client):
    assert client.get("/api/summary", base_url="http://evil.example.com").status_code == 403


def test_rejects_cross_site_writes(client):
    assert post(client, "/api/runs", {}, Origin="https://evil.example.com").status_code == 403
    r = client.post("/api/runs", data="x=1", content_type="application/x-www-form-urlencoded", base_url=BASE)
    assert r.status_code == 415


# --- summary, jobs, status -----------------------------------------------

def test_summary_empty(client):
    s = get(client, "/api/summary").get_json()
    assert s["counts"]["total"] == 0 and s["running"] is False and s["last_run"] is None
    assert s["next_run"] is None and s["resume_found"] is True
    assert s["setup"]["ai_provider"] == "gemini" and "test-key" not in json.dumps(s)


def test_jobs_details_and_status(app, client):
    seed(app, job(1, score=82, reasons=["M365 match"], missing_skills=["Intune"],
                  student=StudentCheck(employment_type="casual", verdict="fits", note="~8 h/week", positives=["casual"])),
         job(2, score=40), job(3))
    seed(app, job(1), draft="Dear Acme...")

    rows = get(client, "/api/jobs?status=seen").get_json()["jobs"]
    assert [r["id"] for r in rows] == ["adzuna:1", "adzuna:2", "adzuna:3"]  # best score first, unscored last
    assert rows[0]["has_draft"] is True and "description" not in rows[0]
    assert len(get(client, "/api/jobs?min_score=70").get_json()["jobs"]) == 1
    assert len(get(client, "/api/jobs?verdict=fits").get_json()["jobs"]) == 1
    assert len(get(client, "/api/jobs?q=helpdesk%202").get_json()["jobs"]) == 1

    d = get(client, "/api/job?id=adzuna:1").get_json()["job"]
    assert d["reasons"] == ["M365 match"] and d["missing_skills"] == ["Intune"]
    assert d["verdict"] == "fits" and d["positives"] == ["casual"] and d["employment_type"] == "casual"
    assert d["draft"] == "Dear Acme..." and d["score"] == 82          # later save without score kept it
    assert "<p>" not in d["description"]

    assert post(client, "/api/job/status", {"id": "adzuna:1", "status": "applied"}).status_code == 200
    assert get(client, "/api/summary").get_json()["counts"]["by_status"]["applied"] == 1
    assert post(client, "/api/job/status", {"id": "adzuna:1", "status": "hacked"}).status_code == 400
    assert post(client, "/api/job/status", {"id": "nope", "status": "skipped"}).status_code == 404
    assert get(client, "/api/job?id=nope").status_code == 404
    assert get(client, "/api/jobs?status=bogus").status_code == 400
    assert get(client, "/api/jobs?min_score=abc").status_code == 400


# --- settings -------------------------------------------------------------

def test_settings_roundtrip_and_validation(client):
    s = get(client, "/api/settings").get_json()["settings"]
    assert s["part_time"] is True and s["schedule_enabled"] is False

    r = post(client, "/api/settings", {"keywords": "soc analyst", "schedule_enabled": True,
                                       "schedule_time": "07:30", "schedule_days": [4, 0, 0], "limit": 5}, method="put")
    assert r.status_code == 200
    saved = get(client, "/api/settings").get_json()["settings"]
    assert saved["keywords"] == "soc analyst" and saved["schedule_days"] == [0, 4] and saved["limit"] == 5

    for bad in ({"limit": 0}, {"limit": "5"}, {"part_time": "yes"}, {"schedule_time": "7am"},
                {"schedule_days": []}, {"schedule_days": [9]}, {"sources": "seek"}, {"evil": 1},
                {"resume": "../../.ssh/id_rsa"}, {"resume": "/etc/passwd"}, {"resume": "notes.md"},
                {"keywords": "x" * 301}):
        r = post(client, "/api/settings", bad, method="put")
        assert r.status_code == 400, bad
        assert r.get_json()["error"]


def test_settings_to_argv():
    s = {**web.DEFAULT_SETTINGS, "sources": "adzuna", "fits_only": True}
    args = cli.parse_args(web.settings_to_argv(s, "x.db"))
    assert args.keywords == s["keywords"] and args.location == "Sydney" and args.sources == "adzuna"
    assert args.part_time and args.fits_only and not args.term_break and args.db == "x.db"
    assert args.limit == 20 and args.max_score == 15 and args.draft_top == 5 and args.resume == "resume.txt"


# --- schedule -------------------------------------------------------------

def test_next_run_time():
    s = {**web.DEFAULT_SETTINGS, "schedule_enabled": True, "schedule_time": "08:00", "schedule_days": [0, 1, 2, 3, 4]}
    fri_7am = datetime(2026, 10, 16, 7, 0)
    assert web.next_run_time(s, fri_7am) == datetime(2026, 10, 16, 8, 0)
    fri_9am = datetime(2026, 10, 16, 9, 0)
    assert web.next_run_time(s, fri_9am) == datetime(2026, 10, 16, 8, 0)            # missed today: due now
    assert web.next_run_time(s, fri_9am, "2026-10-16") == datetime(2026, 10, 19, 8, 0)  # ran today: next Monday
    assert web.next_run_time({**s, "schedule_enabled": False}, fri_9am) is None


def test_scheduler_runs_once_per_day(app, monkeypatch):
    runner = app.config["RUNNER"]
    store = JobStore(runner.db)
    store.save_settings({**web.DEFAULT_SETTINGS, "schedule_enabled": True, "schedule_time": "08:00",
                         "schedule_days": list(range(7))})
    store.close()
    started = []
    monkeypatch.setattr(runner, "start", lambda trigger: started.append(trigger) or True)
    sched = web.Scheduler(runner.db, runner)
    assert sched.tick(datetime(2026, 10, 17, 7, 59)) is False
    assert sched.tick(datetime(2026, 10, 17, 8, 0)) is True
    assert sched.tick(datetime(2026, 10, 17, 8, 1)) is False   # already ran today
    assert started == ["schedule"]


# --- run now, end to end ----------------------------------------------------

def test_run_now_records_run_and_jobs(app, client, monkeypatch):
    monkeypatch.setenv("VISA_FORTNIGHT_CAP", "48")
    monkeypatch.setenv("COMMITTED_FORTNIGHT_HOURS", "28")
    monkeypatch.setitem(cli.sources_mod.SOURCES, "adzuna", lambda **kw: [job(1), job(2), job(3, description="Full-time role")])
    monkeypatch.setattr(cli, "score_job", lambda j, r: setattr(j, "score", 90 if j.id.endswith("1") else 60))
    monkeypatch.setattr(cli, "draft_application", lambda j, r: f"Note for {j.title}")
    post(client, "/api/settings", {"sources": "adzuna", "keywords": "helpdesk", "draft_top": 1}, method="put")

    r = post(client, "/api/runs", {})
    assert r.status_code == 202
    app.config["RUNNER"].thread.join(5)

    runs = get(client, "/api/runs").get_json()["runs"]
    assert len(runs) == 1
    run = get(client, f"/api/runs/{runs[0]['id']}").get_json()["run"]
    assert run["status"] == "done" and run["trigger"] == "manual"
    assert (run["fetched"], run["matched"], run["new"], run["scored"], run["drafted"], run["shown"]) == (3, 3, 2, 2, 1, 2)
    assert "Dropped 1 full-time" in run["log"] and run["params"]["keywords"] == "helpdesk"

    j1 = get(client, "/api/job?id=adzuna:1").get_json()["job"]
    assert j1["score"] == 90 and j1["draft"] == "Note for Helpdesk 1" and j1["verdict"] == "fits" and j1["run_id"] == run["id"]
    assert len(get(client, f"/api/jobs?run_id={run['id']}").get_json()["jobs"]) == 2

    s = get(client, "/api/summary").get_json()
    assert s["last_run"]["id"] == run["id"] and s["top_jobs"][0]["id"] == "adzuna:1"


def test_failed_run_is_recorded(app, client, monkeypatch):
    def down(**kw):
        raise cli.SourceError("adzuna down")
    monkeypatch.setitem(cli.sources_mod.SOURCES, "adzuna", down)
    post(client, "/api/settings", {"sources": "adzuna"}, method="put")
    post(client, "/api/runs", {})
    app.config["RUNNER"].thread.join(5)
    run = get(client, "/api/runs").get_json()["runs"][0]
    assert run["status"] == "failed" and "No jobs fetched" in run["error"]
    assert app.config["RUNNER"].running is False


def test_only_one_run_at_a_time(app, client):
    runner = app.config["RUNNER"]
    runner._lock.acquire()
    try:
        r = post(client, "/api/runs", {})
        assert r.status_code == 409 and "already running" in r.get_json()["error"]
        assert get(client, "/api/summary").get_json()["running"] is True
    finally:
        runner._lock.release()


def test_stale_running_runs_marked_failed_on_start(tmp_path):
    db = str(tmp_path / "j.db")
    store = JobStore(db)
    rid = store.start_run("manual", {})
    store.close()
    web.create_app(db, start_scheduler=False)
    store = JobStore(db)
    assert store.get_run(rid)["status"] == "failed" and "Interrupted" in store.get_run(rid)["error"]
    store.close()


def test_cli_runs_show_in_history(tmp_path, monkeypatch, capsys):
    monkeypatch.setitem(cli.sources_mod.SOURCES, "adzuna", lambda **kw: [job(1)])
    db = str(tmp_path / "j.db")
    cli.main(["--sources", "adzuna", "--db", db])
    store = JobStore(db)
    [run] = store.list_runs()
    assert run["trigger"] == "cli" and run["status"] == "done" and run["shown"] == 1
    store.close()


# --- upgrading an old database ----------------------------------------------

def test_old_database_is_upgraded(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, source TEXT, title TEXT, company TEXT, url TEXT,
                    score INTEGER, status TEXT NOT NULL DEFAULT 'seen', first_seen TEXT NOT NULL, updated TEXT NOT NULL)""")
    conn.execute("INSERT INTO jobs VALUES ('adzuna:9','adzuna','Old','Co','u',77,'applied','2026-10-01','2026-10-01')")
    conn.commit()
    conn.close()
    store = JobStore(str(db))
    old = store.get_job("adzuna:9")
    assert old["status"] == "applied" and old["score"] == 77 and old["reasons"] == [] and old["draft"] is None
    store.save_job(job(9))
    assert store.get_job("adzuna:9")["status"] == "applied"  # never downgraded
    store.close()
