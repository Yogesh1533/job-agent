"""Jobs straight from company careers pages, via their applicant tracking system (ATS).

Most companies host their careers page on one of these ATSs, which all have
public, no-key JSON job feeds. companies.json lists which company uses which.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from job_agent.models import Job

USER_AGENT = "job-agent/0.3 (personal job search tool)"
TIMEOUT = 15

ATS_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
}


class ATSError(Exception):
    pass


def normalise_type(value):
    """Map an ATS employment-type label to one of our types."""
    v = re.sub(r"[^a-z]", "", (value or "").lower())
    if not v:
        return ""
    if "intern" in v or "graduate" in v or "student" in v:
        return "internship"
    if "casual" in v:
        return "casual"
    if "part" in v:
        return "part_time"
    if "contract" in v or "temp" in v or "fixedterm" in v:
        return "contract"
    if "full" in v or "permanent" in v:
        return "full_time"
    return ""


def _mode(value):
    from job_agent.classify import normalise_mode
    return normalise_mode(value)


def _salary(low, high, currency="AUD", interval="year"):
    from job_agent.classify import fmt_salary
    symbol = "$" if currency in (None, "", "AUD", "USD") else f"{currency} "
    if currency == "USD":
        symbol = "US$"
    interval = {"per-year-salary": "year", "per-hour-wage": "hour", "per-month-salary": "month",
                "per-week-salary": "week", "per-day-wage": "day"}.get(interval, interval or "year")
    return fmt_salary(low, high, symbol, interval)


def _html_to_text(html):
    import html as html_lib
    return re.sub(r"<[^>]+>", " ", html_lib.unescape(html or ""))


# --- parsers (one per ATS) ----------------------------------------------

def parse_greenhouse(data, company):
    jobs = []
    for j in (data or {}).get("jobs") or []:
        jobs.append(Job(
            id=f"greenhouse:{company['slug']}:{j.get('id')}",
            title=j.get("title") or "",
            company=j.get("company_name") or company["name"],
            location=(j.get("location") or {}).get("name") or "",
            url=j.get("absolute_url") or "",
            description=_html_to_text(j.get("content")),
            tags=[],
            source="greenhouse",
            category=", ".join(d.get("name", "") for d in (j.get("departments") or []) if d.get("name")),
        ))
    return jobs


def parse_lever(data, company):
    jobs = []
    for j in data or []:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location")]
        location = ", ".join(l for l in locs if l)
        if (j.get("workplaceType") or "").lower() == "remote":
            location = f"{location} (Remote)".strip()
        jobs.append(Job(
            id=f"lever:{company['slug']}:{j.get('id')}",
            title=j.get("text") or "",
            company=company["name"],
            location=location,
            url=j.get("hostedUrl") or "",
            description=j.get("descriptionPlain") or "",
            tags=[t for t in [cats.get("team")] if t],
            source="lever",
            employment_type=normalise_type(cats.get("commitment")),
            work_mode=_mode(j.get("workplaceType")),
            salary_text=_salary((j.get("salaryRange") or {}).get("min"), (j.get("salaryRange") or {}).get("max"),
                                (j.get("salaryRange") or {}).get("currency"), (j.get("salaryRange") or {}).get("interval"))
            if j.get("salaryRange") else "",
            category=cats.get("team") or "",
        ))
    return jobs


def parse_ashby(data, company):
    jobs = []
    for j in (data or {}).get("jobs") or []:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location")] + [
            (s or {}).get("location") for s in (j.get("secondaryLocations") or [])
        ]
        location = ", ".join(l for l in locs if l)
        if j.get("isRemote"):
            location = f"{location} (Remote)".strip()
        jobs.append(Job(
            id=f"ashby:{company['slug']}:{j.get('id')}",
            title=j.get("title") or "",
            company=company["name"],
            location=location,
            url=j.get("jobUrl") or "",
            description=j.get("descriptionPlain") or "",
            tags=[t for t in [j.get("department")] if t],
            source="ashby",
            employment_type=normalise_type(j.get("employmentType")),
            work_mode=_mode(j.get("workplaceType")) or ("remote" if j.get("isRemote") else ""),
            category=j.get("department") or "",
        ))
    return jobs


def parse_workable(data, company):
    jobs = []
    for j in (data or {}).get("jobs") or []:
        location = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        if j.get("telecommuting"):
            location = f"{location} (Remote)".strip()
        jobs.append(Job(
            id=f"workable:{company['slug']}:{j.get('shortcode') or j.get('code')}",
            title=j.get("title") or "",
            company=(data or {}).get("name") or company["name"],
            location=location,
            url=j.get("url") or j.get("shortlink") or "",
            description=_html_to_text(j.get("description")),
            tags=[t for t in [j.get("department")] if t],
            source="workable",
            employment_type=normalise_type(j.get("employment_type")),
            work_mode="remote" if j.get("telecommuting") else "",
            category=j.get("department") or "",
        ))
    return jobs


def parse_smartrecruiters(data, company):
    jobs = []
    for j in (data or {}).get("content") or []:
        loc = j.get("location") or {}
        location = ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
        if loc.get("remote"):
            location = f"{location} (Remote)".strip()
        ident = (j.get("company") or {}).get("identifier") or company["slug"]
        jobs.append(Job(
            id=f"smartrecruiters:{company['slug']}:{j.get('id')}",
            title=j.get("name") or "",
            company=(j.get("company") or {}).get("name") or company["name"],
            location=location,
            url=f"https://jobs.smartrecruiters.com/{ident}/{j.get('id')}",
            description="",  # list endpoint has no description; scoring uses title/location
            tags=[t for t in [(j.get("department") or {}).get("label")] if t],
            source="smartrecruiters",
            employment_type=normalise_type((j.get("typeOfEmployment") or {}).get("label")),
            work_mode="hybrid" if loc.get("hybrid") else "remote" if loc.get("remote") else "",
            category=(j.get("department") or {}).get("label") or "",
        ))
    return jobs


PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
    "workable": parse_workable,
    "smartrecruiters": parse_smartrecruiters,
}


# --- fetching -----------------------------------------------------------

def fetch_company(company):
    ats = company.get("ats")
    if ats not in ATS_URLS:
        raise ATSError(f"{company.get('name')}: unknown ATS '{ats}'")
    url = ATS_URLS[ats].format(slug=company["slug"])
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise ATSError(f"{company.get('name')} ({ats}): {e}") from e
    return PARSERS[ats](data, company)


def load_companies(path):
    with open(path) as f:
        data = json.load(f)
    companies = data.get("companies", data) if isinstance(data, dict) else data
    return [c for c in companies if c.get("ats") and c.get("slug")]


def fetch_companies(companies, workers=8):
    """Fetch every company in parallel. Returns (jobs, errors)."""
    jobs, errors = [], []

    def one(c):
        try:
            return fetch_company(c), None
        except ATSError as e:
            return [], str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for found, err in pool.map(one, companies):
            jobs.extend(found)
            if err:
                errors.append(err)
    return jobs, errors


# --- discovery ----------------------------------------------------------

def slug_candidates(name):
    base = name.strip().lower()
    words = re.findall(r"[a-z0-9]+", base)
    cands = ["".join(words), "-".join(words)]
    if len(words) > 1:
        cands.append(words[0])
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def discover(name):
    """Try each ATS with likely slugs. Returns [{name, ats, slug, jobs}] for boards that exist."""
    hits = []
    for slug in slug_candidates(name):
        for ats in ATS_URLS:
            company = {"name": name, "ats": ats, "slug": slug}
            try:
                jobs = fetch_company(company)
            except ATSError:
                continue
            # SmartRecruiters answers any slug with an empty list, so require jobs there
            if ats == "smartrecruiters" and not jobs:
                continue
            hits.append({**company, "jobs": len(jobs)})
    return hits


def add_company(path, company):
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {"companies": []}
    companies = data["companies"] if isinstance(data, dict) else data
    key = (company["ats"], company["slug"])
    if any((c.get("ats"), c.get("slug")) == key for c in companies):
        return False
    companies.append({k: company[k] for k in ("name", "ats", "slug")})
    with open(path, "w") as f:
        json.dump(data if isinstance(data, dict) else {"companies": companies}, f, indent=2)
        f.write("\n")
    return True
