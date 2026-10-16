import sys


from job_agent import main as main_mod
from job_agent.drafter import DEFAULT_MODEL, get_model
from job_agent.matcher import filter_jobs
from job_agent.models import Job
from job_agent.sources import parse_remoteok_entries


def make_job(title="Engineer", description="", location="Remote", tags=None, id="1"):
    return Job(id=id, title=title, company="Acme", location=location,
               url="https://example.com", description=description, tags=tags or [])


# --- keyword matching ---------------------------------------------------

def test_ai_does_not_match_email():
    job = make_job("Senior Accountant", "Maintain ledgers and reply to email.")
    assert filter_jobs([job], keywords=["ai"]) == []


def test_go_does_not_match_good():
    job = make_job("Sales Rep", "Good communication skills required.")
    assert filter_jobs([job], keywords=["go"]) == []


def test_whole_word_keyword_still_matches():
    job = make_job("AI Engineer", "Build LLM apps in Python.")
    assert filter_jobs([job], keywords=["ai"]) == [job]
    assert filter_jobs([job], keywords=["python"]) == [job]


def test_keywords_with_symbols():
    job = make_job("Developer", "Experience with C++ and C# required.")
    assert filter_jobs([job], keywords=["c++"]) == [job]
    assert filter_jobs([make_job("Dev", "Uses C only")], keywords=["c++"]) == []


# --- location filter ----------------------------------------------------

def test_remote_filter_drops_onsite_jobs():
    remote = make_job(location="Remote", id="1")
    worldwide = make_job(location="Worldwide", id="2")
    onsite = make_job(location="New York, NY", id="3")
    assert filter_jobs([remote, worldwide, onsite], location="remote") == [remote, worldwide]


def test_remote_tag_counts_as_remote():
    job = make_job(location="USA", tags=["remote", "python"])
    assert filter_jobs([job], location="remote") == [job]


def test_city_location_filter():
    syd = make_job(location="Sydney, NSW", id="1")
    mel = make_job(location="Melbourne", id="2")
    assert filter_jobs([syd, mel], location="Sydney") == [syd]


# --- null-safe fields ---------------------------------------------------

def test_null_tags_and_location_do_not_crash():
    entries = [
        {"legal": "notice"},
        {"id": 1, "position": "Dev", "company": None, "location": None,
         "tags": None, "description": None},
    ]
    jobs = parse_remoteok_entries(entries)
    assert len(jobs) == 1
    assert jobs[0].tags == [] and jobs[0].location == "Remote"
    assert filter_jobs(jobs, keywords=["dev"], location="remote") == jobs


def test_filter_handles_none_fields_on_job():
    job = Job(id="1", title=None, company=None, location=None, url="",
              description=None, tags=None)
    assert filter_jobs([job], keywords=["python"]) == []


# --- model config -------------------------------------------------------

def test_model_from_env(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    assert get_model() == DEFAULT_MODEL == "claude-sonnet-5-5"
    monkeypatch.setenv("MODEL", "claude-haiku-5-5")
    assert get_model() == "claude-haiku-5-5"


# --- error handling -----------------------------------------------------

def test_one_failed_draft_does_not_lose_other_results(tmp_path, monkeypatch):
    jobs = [make_job("Python Dev", id="1"), make_job("Python Lead", id="2")]
    monkeypatch.setattr(main_mod, "fetch_remoteok_jobs", lambda: jobs)

    def fake_draft(job, resume):
        if job.id == "1":
            raise RuntimeError("API down")
        return "Great fit."
    monkeypatch.setattr(main_mod, "draft_application", fake_draft)

    resume = tmp_path / "resume.txt"
    resume.write_text("My resume")
    out = tmp_path / "results.md"
    monkeypatch.setattr(sys, "argv", ["job_agent", "--keywords", "python",
                                      "--resume", str(resume), "--output", str(out)])
    main_mod.main()

    text = out.read_text()
    assert "Python Dev" in text and "Draft failed: RuntimeError: API down" in text
    assert "Python Lead" in text and "Great fit." in text
