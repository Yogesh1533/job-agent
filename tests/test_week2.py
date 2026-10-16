import pytest

from job_agent import main as main_mod
from job_agent import sources
from job_agent.models import Job
from job_agent.scorer import ScoreError, parse_score, score_job, sort_by_score
from job_agent.store import JobStore


def make_job(id="remoteok:1", title="Engineer", company="Acme", score=None):
    j = Job(id=id, title=title, company=company, location="Sydney NSW",
            url="https://x", description="Python AI security", tags=[], source=id.split(":")[0])
    j.score = score
    return j


# --- Adzuna -------------------------------------------------------------

ADZUNA_SAMPLE = {"results": [
    {"id": "4001", "title": "AI Engineer", "company": {"display_name": "Canva"},
     "location": {"display_name": "Sydney, New South Wales"},
     "redirect_url": "https://adzuna.com.au/land/4001", "description": "Build LLM apps",
     "category": {"label": "IT Jobs"}},
    {"id": "4002", "title": "Security Analyst", "company": None, "location": None,
     "description": None, "category": None},
    {"title": "no id, skipped"},
]}


def test_parse_adzuna_results():
    jobs = sources.parse_adzuna_results(ADZUNA_SAMPLE)
    assert [j.id for j in jobs] == ["adzuna:4001", "adzuna:4002"]
    assert jobs[0].company == "Canva" and jobs[0].location.startswith("Sydney")
    assert jobs[0].tags == ["IT Jobs"] and jobs[0].source == "adzuna"
    assert jobs[1].company == "" and jobs[1].tags == []


def test_adzuna_needs_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPANIES_FILE", str(tmp_path / "none.json"))
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    assert sources.default_sources() == ["remoteok"]
    with pytest.raises(sources.SourceError):
        sources.fetch_adzuna_jobs()


def test_adzuna_request(monkeypatch, tmp_path):
    monkeypatch.setenv("COMPANIES_FILE", str(tmp_path / "none.json"))
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    monkeypatch.setenv("ADZUNA_APP_ID", "id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "key")
    calls = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return ADZUNA_SAMPLE

    def fake_get(url, params, headers, timeout):
        calls.append((url, params))
        return Resp()
    monkeypatch.setattr(sources.requests, "get", fake_get)

    jobs = sources.fetch_adzuna_jobs(keywords=["ai", "cyber security"], location="Sydney")
    url, params = calls[0]
    assert url == "https://api.adzuna.com/v1/api/jobs/au/search/1"
    assert params["what_or"] == "ai cyber security" and params["where"] == "Sydney"
    assert len(calls) == 1  # fewer results than a full page, so no page 2
    assert len(jobs) == 2
    assert sources.default_sources() == ["adzuna", "remoteok"]


# --- scoring ------------------------------------------------------------

def test_parse_score_plain_and_fenced():
    r = parse_score('```json\n{"score": 82, "reasons": ["Python match"], "missing_skills": ["AWS"]}\n```')
    assert r == {"score": 82, "reasons": ["Python match"], "missing_skills": ["AWS"]}


def test_parse_score_clamps_and_coerces():
    r = parse_score('Here: {"score": "130", "reasons": "one reason", "missing_skills": null}')
    assert r["score"] == 100 and r["reasons"] == ["one reason"] and r["missing_skills"] == []


@pytest.mark.parametrize("bad", ["no json here", '{"score": "high"}', "{not json}"])
def test_parse_score_rejects_bad_replies(bad):
    with pytest.raises(ScoreError):
        parse_score(bad)


def test_score_job_sets_fields():
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                class R: content = [type("T", (), {"text": '{"score": 70, "reasons": ["a"], "missing_skills": ["b"]}'})]
                return R
    job = make_job()
    score_job(job, "resume", client=FakeClient)
    assert (job.score, job.reasons, job.missing_skills) == (70, ["a"], ["b"])


def test_sort_by_score_puts_unscored_last():
    a, b, c = make_job("x:1", score=40), make_job("x:2", score=None), make_job("x:3", score=90)
    assert sort_by_score([a, b, c]) == [c, a, b]


# --- memory -------------------------------------------------------------

def test_store_seen_applied_skipped(tmp_path):
    store = JobStore(str(tmp_path / "jobs.db"))
    job = make_job("adzuna:1", score=75)
    store.mark_seen(job)
    assert store.known_ids() == {"adzuna:1"}
    assert store.set_status("adzuna:1", "applied")
    store.mark_seen(job)  # seeing it again must not undo "applied"
    assert store.list("applied")[0]["score"] == 75
    assert not store.set_status("missing:9", "skipped")
    with pytest.raises(ValueError):
        store.set_status("adzuna:1", "bogus")


def test_repeat_run_hides_seen_jobs(tmp_path, monkeypatch, capsys):
    jobs = [make_job("adzuna:1", "AI Engineer"), make_job("adzuna:2", "Security Analyst")]
    monkeypatch.setitem(main_mod.sources_mod.SOURCES, "adzuna", lambda **kw: list(jobs))
    db = str(tmp_path / "jobs.db")
    base = ["--sources", "adzuna", "--db", db]

    main_mod.main(base)
    assert "AI Engineer" in capsys.readouterr().out

    main_mod.main(["--mark-skipped", "adzuna:2"] + base)
    main_mod.main(base)
    assert "No new matching jobs" in capsys.readouterr().out

    main_mod.main(base + ["--show-seen"])
    assert "Security Analyst" in capsys.readouterr().out


def test_scored_jobs_sorted_and_only_top_n_drafted(tmp_path, monkeypatch, capsys):
    jobs = [make_job(f"adzuna:{i}", f"Job{i}") for i in range(4)]
    monkeypatch.setitem(main_mod.sources_mod.SOURCES, "adzuna", lambda **kw: list(jobs))
    monkeypatch.setattr(main_mod, "score_job", lambda job, r: setattr(job, "score", int(job.id[-1]) * 10))
    drafted = []
    monkeypatch.setattr(main_mod, "draft_application", lambda job, r: drafted.append(job.id) or "note")
    resume = tmp_path / "r.txt"
    resume.write_text("resume")

    main_mod.main(["--sources", "adzuna", "--db", str(tmp_path / "j.db"),
                   "--resume", str(resume), "--draft-top", "2"])
    out = capsys.readouterr().out
    assert out.index("[30] Job3") < out.index("[20] Job2") < out.index("[0] Job0")
    assert drafted == ["adzuna:3", "adzuna:2"]


def test_dedupe_across_sources():
    a = make_job("adzuna:1", "AI Engineer", "Canva")
    b = make_job("remoteok:9", "AI Engineer", "canva ")
    assert main_mod.dedupe([a, b]) == [a]


def test_one_source_failing_keeps_the_other(monkeypatch, capsys):
    def boom(**kw):
        raise sources.SourceError("adzuna down")
    monkeypatch.setitem(main_mod.sources_mod.SOURCES, "adzuna", boom)
    monkeypatch.setitem(main_mod.sources_mod.SOURCES, "remoteok", lambda **kw: [make_job()])
    assert len(main_mod.fetch_all(["adzuna", "remoteok"], None, None)) == 1
    assert "adzuna down" in capsys.readouterr().err
