import json

import pytest

from job_agent import ats, sources
from job_agent import main as main_mod
from job_agent.models import Job
from job_agent.student import available_fortnight_hours, check_job, estimate_hours


def job(description="", title="Engineer", employment_type="", id="x:1", tags=None):
    return Job(id=id, title=title, company="Acme", location="Sydney NSW", url="https://x",
               description=description, tags=tags or [], source="x", employment_type=employment_type)


# --- company ATS parsers ------------------------------------------------

CO = {"name": "Acme", "ats": "", "slug": "acme"}


def test_parse_greenhouse():
    data = {"jobs": [{"id": 7, "title": "Security Intern", "company_name": "Acme Pty",
                      "location": {"name": "Sydney"}, "absolute_url": "https://gh/7",
                      "content": "&lt;p&gt;Part-time, 20 hours per week&lt;/p&gt;"}]}
    [j] = ats.parse_greenhouse(data, CO)
    assert j.id == "greenhouse:acme:7" and j.company == "Acme Pty" and j.location == "Sydney"
    assert "20 hours per week" in j.description and "<p>" not in j.description


def test_parse_lever():
    data = [{"id": "a1", "text": "Support Engineer", "hostedUrl": "https://lever/a1",
             "descriptionPlain": "Help users", "workplaceType": "hybrid",
             "categories": {"commitment": "Part-time", "location": "Sydney", "team": "Support",
                            "allLocations": ["Sydney", "Melbourne"]}}]
    [j] = ats.parse_lever(data, CO)
    assert j.employment_type == "part_time" and j.location == "Sydney, Melbourne" and j.tags == ["Support"]


def test_parse_ashby_skips_unlisted():
    data = {"jobs": [
        {"id": "1", "title": "Grad Engineer", "employmentType": "Intern", "location": "Sydney",
         "isRemote": True, "jobUrl": "https://ashby/1", "descriptionPlain": "x", "isListed": True},
        {"id": "2", "title": "Hidden", "isListed": False},
    ]}
    [j] = ats.parse_ashby(data, CO)
    assert j.employment_type == "internship" and j.location == "Sydney (Remote)"


def test_parse_workable_and_smartrecruiters():
    [w] = ats.parse_workable({"name": "Acme", "jobs": [
        {"title": "IT Support", "shortcode": "AB1", "employment_type": "Casual",
         "city": "Sydney", "country": "Australia", "url": "https://wk/AB1"}]}, CO)
    assert w.id == "workable:acme:AB1" and w.employment_type == "casual"
    [s] = ats.parse_smartrecruiters({"content": [
        {"id": "99", "name": "Helpdesk", "company": {"identifier": "Acme1", "name": "Acme"},
         "location": {"city": "Sydney", "country": "au"}, "typeOfEmployment": {"label": "Full-time"}}]}, CO)
    assert s.url == "https://jobs.smartrecruiters.com/Acme1/99" and s.employment_type == "full_time"
    assert s.location == "Sydney, AU"


@pytest.mark.parametrize("label,expected", [
    ("Part-time", "part_time"), ("PartTime", "part_time"), ("Casual", "casual"),
    ("Intern", "internship"), ("Full-time", "full_time"), ("Fixed Term", "contract"), ("", ""),
])
def test_normalise_type(label, expected):
    assert ats.normalise_type(label) == expected


def test_fetch_companies_keeps_going_when_one_fails(monkeypatch):
    def fake(company):
        if company["slug"] == "bad":
            raise ats.ATSError("bad: 404")
        return [job(id=f"lever:{company['slug']}:1")]
    monkeypatch.setattr(ats, "fetch_company", fake)
    jobs, errors = ats.fetch_companies([{"slug": "good"}, {"slug": "bad"}])
    assert len(jobs) == 1 and errors == ["bad: 404"]


def test_discover_and_add_company(tmp_path, monkeypatch):
    def fake(company):
        if company["ats"] == "lever" and company["slug"] == "deputy":
            return [job(), job()]
        if company["ats"] == "smartrecruiters":
            return []  # answers any slug, must be ignored when empty
        raise ats.ATSError("404")
    monkeypatch.setattr(ats, "fetch_company", fake)
    hits = ats.discover("Deputy")
    assert hits == [{"name": "Deputy", "ats": "lever", "slug": "deputy", "jobs": 2}]

    path = tmp_path / "companies.json"
    path.write_text(json.dumps({"_note": "x", "companies": []}))
    assert ats.add_company(str(path), hits[0])
    assert not ats.add_company(str(path), hits[0])  # no duplicates
    data = json.loads(path.read_text())
    assert data["_note"] == "x" and data["companies"] == [{"name": "Deputy", "ats": "lever", "slug": "deputy"}]
    assert ats.load_companies(str(path))[0]["slug"] == "deputy"


def test_seed_companies_file_is_valid():
    companies = ats.load_companies("companies.json")
    assert companies and all(c["ats"] in ats.ATS_URLS for c in companies)


# --- Jooble -------------------------------------------------------------

def test_parse_jooble():
    jobs = sources.parse_jooble_results({"jobs": [
        {"id": 5, "title": "Casual IT Support", "company": "Uni", "location": "Sydney NSW",
         "snippet": "students welcome", "type": "Part-time", "link": "https://j/5", "source": "seek"},
        {"title": "no id or link"},
    ]})
    assert len(jobs) == 1 and jobs[0].id == "jooble:5" and jobs[0].employment_type == "part_time"


def test_jooble_request(monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    sent = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"jobs": []}
    monkeypatch.setattr(sources.requests, "post",
                        lambda url, json, headers, timeout: sent.append((url, json)) or Resp())
    sources.fetch_jooble_jobs(keywords=["it support"], location="Sydney", part_time=True)
    url, body = sent[0]
    assert url == "https://au.jooble.org/api/k"
    assert body == {"keywords": "it support part time", "location": "Sydney", "page": "1"}


def test_adzuna_part_time_flag_and_type(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "i")
    monkeypatch.setenv("ADZUNA_APP_KEY", "k")
    seen = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"results": [{"id": "1", "title": "Helpdesk", "contract_time": "part_time"}]}
    monkeypatch.setattr(sources.requests, "get", lambda url, params, headers, timeout: seen.append(params) or Resp())
    [j] = sources.fetch_adzuna_jobs(part_time=True)
    assert seen[0]["part_time"] == 1 and j.employment_type == "part_time"


# --- student / visa checks ----------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("15-20 hours per week", (15.0, 20.0)),
    ("about 20 hrs/week", (20.0, 20.0)),
    ("2-3 days a week", (15.2, 22.8)),
    ("40 hours per fortnight", (20.0, 20.0)),
    ("0.4 FTE", (15.2, 15.2)),
    ("great team", (None, None)),
])
def test_estimate_hours(text, expected):
    assert estimate_hours(text.lower()) == expected


def test_available_hours(monkeypatch):
    monkeypatch.setenv("VISA_FORTNIGHT_CAP", "48")
    monkeypatch.setenv("COMMITTED_FORTNIGHT_HOURS", "42")
    assert available_fortnight_hours() == 6
    assert available_fortnight_hours(term_break=True) is None
    monkeypatch.setenv("COMMITTED_FORTNIGHT_HOURS", "60")
    assert available_fortnight_hours() == 0


def test_verdicts():
    assert check_job(job("Casual, 8 hours per week"), available=20).verdict == "fits"
    assert check_job(job("Part-time 8-15 hours per week"), available=20).verdict == "partly"
    assert check_job(job("Part-time, 3 days a week"), available=6).verdict == "over_cap"
    assert check_job(job("Casual role, flexible hours"), available=6).verdict == "unknown_hours"
    assert check_job(job("Part-time, 20 hours per week"), available=None).verdict == "no_cap"


@pytest.mark.parametrize("text", [
    "Must be an Australian citizen",
    "Australian citizens or permanent residents only",
    "Ability to obtain NV1 clearance",
    "Baseline clearance required",
    "You must have full working rights in Australia",
    "unrestricted work rights",
])
def test_blockers(text):
    c = check_job(job("Part-time. " + text), available=48)
    assert c.verdict == "blocked" and c.blockers


def test_part_time_friendliness():
    assert check_job(job(employment_type="full_time"), 48).is_part_time_friendly is False
    assert check_job(job("Students welcome, flexible hours"), 48).is_part_time_friendly is True
    assert check_job(job("Great company"), 48).is_part_time_friendly is False
    assert check_job(job(title="IT Support Intern"), 48).is_part_time_friendly is True


# --- end to end: --part-time ---------------------------------------------

def test_part_time_mode_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VISA_FORTNIGHT_CAP", "48")
    monkeypatch.setenv("COMMITTED_FORTNIGHT_HOURS", "28")  # 20 free
    jobs = [
        job("Full-time security analyst", title="Security Analyst", id="a:1"),
        job("Part-time 25 hours per week", title="IT Support Over", id="a:2"),
        job("Casual helpdesk, 8 hours per week, students welcome", title="Helpdesk Fits", id="a:3"),
        job("Part-time. Must be an Australian citizen", title="Gov Blocked", id="a:4"),
        job("Part-time, hours flexible", title="Unknown Hours", id="a:5"),
    ]
    monkeypatch.setitem(main_mod.sources_mod.SOURCES, "adzuna", lambda **kw: list(jobs))
    main_mod.main(["--sources", "adzuna", "--part-time", "--db", str(tmp_path / "j.db")])
    cap = capsys.readouterr()
    out = cap.out
    assert "Security Analyst" not in out and "Gov Blocked" not in out
    assert out.index("Helpdesk Fits") < out.index("Unknown Hours") < out.index("IT Support Over")
    assert "fits your hours" in out and "Student-friendly signs: casual, students" in out
    assert "20 of 48 h/fortnight free" in cap.err and "Dropped 1 job(s) needing citizenship" in cap.err

    main_mod.main(["--sources", "adzuna", "--part-time", "--fits-only", "--show-seen",
                   "--db", str(tmp_path / "j.db")])
    out = capsys.readouterr().out
    assert "Helpdesk Fits" in out and "IT Support Over" not in out and "Unknown Hours" not in out
