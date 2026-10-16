import os
import sys

import requests

from job_agent import log
from job_agent.models import Job

REMOTEOK_API_URL = "https://remoteok.com/api"
ADZUNA_API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
USER_AGENT = "job-agent/0.3 (personal job search tool)"


class SourceError(Exception):
    """Raised when a job source can't be fetched or parsed."""


def _fmt_salary(low, high, symbol):
    from job_agent.classify import fmt_salary
    return fmt_salary(low, high, symbol)


# --- RemoteOK -----------------------------------------------------------

def _remoteok_type(tags):
    tags = {str(t).lower() for t in (tags or [])}
    if tags & {"part time", "part-time", "parttime"}:
        return "part_time"
    if tags & {"intern", "internship"}:
        return "internship"
    return ""


def parse_remoteok_entries(entries):
    jobs = []
    for entry in entries or []:
        if not isinstance(entry, dict) or "id" not in entry:
            continue  # first item in the RemoteOK feed is a legal notice
        jobs.append(
            Job(
                id=f"remoteok:{entry.get('id')}",
                title=entry.get("position") or "",
                company=entry.get("company") or "",
                location=entry.get("location") or "Remote",
                url=entry.get("url") or "",
                description=entry.get("description") or "",
                tags=[t for t in (entry.get("tags") or []) if t],
                source="remoteok",
                employment_type=_remoteok_type(entry.get("tags")),
                work_mode="remote",
                salary_text=_fmt_salary(entry.get("salary_min"), entry.get("salary_max"), "US$"),
            )
        )
    return jobs


def fetch_remoteok_jobs(keywords=None, location=None, part_time=False):
    try:
        response = requests.get(REMOTEOK_API_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        entries = response.json()
    except (requests.RequestException, ValueError) as e:
        raise SourceError(f"Could not fetch RemoteOK jobs: {e}") from e
    return parse_remoteok_entries(entries)


# --- Adzuna (Australia) -------------------------------------------------

def adzuna_configured():
    return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))


def _adzuna_type(r):
    if r.get("contract_time") == "part_time":
        return "part_time"
    if r.get("contract_type") == "contract":
        return "contract"
    if r.get("contract_time") == "full_time":
        return "full_time"
    return ""


def parse_adzuna_results(data):
    jobs = []
    for r in (data or {}).get("results") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        category = (r.get("category") or {}).get("label")
        jobs.append(
            Job(
                id=f"adzuna:{r['id']}",
                title=r.get("title") or "",
                company=(r.get("company") or {}).get("display_name") or "",
                location=(r.get("location") or {}).get("display_name") or "",
                url=r.get("redirect_url") or "",
                description=r.get("description") or "",
                tags=[category] if category else [],
                source="adzuna",
                employment_type=_adzuna_type(r),
                salary_text="" if str(r.get("salary_is_predicted")) == "1"
                else _fmt_salary(r.get("salary_min"), r.get("salary_max"), "$"),
                category=category or "",
            )
        )
    return jobs


def fetch_adzuna_jobs(keywords=None, location=None, part_time=False, pages=2, per_page=50):
    """Search Adzuna Australia. Keys come from ADZUNA_APP_ID / ADZUNA_APP_KEY in .env."""
    if not adzuna_configured():
        raise SourceError("Adzuna keys missing: set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env")

    params = {
        "app_id": os.getenv("ADZUNA_APP_ID"),
        "app_key": os.getenv("ADZUNA_APP_KEY"),
        "results_per_page": per_page,
        "content-type": "application/json",
    }
    if keywords:
        params["what_or"] = " ".join(keywords)  # broad on purpose; matcher narrows it
    if location and location.lower() != "remote":
        params["where"] = location
    if part_time:
        params["part_time"] = 1
    country = os.getenv("ADZUNA_COUNTRY", "au")

    jobs = []
    for page in range(1, pages + 1):
        try:
            response = requests.get(
                ADZUNA_API_URL.format(country=country, page=page),
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=15,
            )
            response.raise_for_status()
            batch = parse_adzuna_results(response.json())
        except (requests.RequestException, ValueError) as e:
            if jobs:
                break  # keep what we already have
            raise SourceError(f"Could not fetch Adzuna jobs: {e}") from e
        jobs.extend(batch)
        if len(batch) < per_page:
            break
    return jobs


# --- Jooble (aggregator, optional key) ---------------------------------

JOOBLE_API_URL = "https://{host}/api/{key}"


def jooble_configured():
    return bool(os.getenv("JOOBLE_API_KEY"))


def parse_jooble_results(data):
    from job_agent.ats import normalise_type
    jobs = []
    for r in (data or {}).get("jobs") or []:
        if not isinstance(r, dict) or not (r.get("id") or r.get("link")):
            continue
        jobs.append(
            Job(
                id=f"jooble:{r.get('id') or r.get('link')}",
                title=r.get("title") or "",
                company=r.get("company") or "",
                location=r.get("location") or "",
                url=r.get("link") or "",
                description=r.get("snippet") or "",
                tags=[t for t in [r.get("source")] if t],
                source="jooble",
                employment_type=normalise_type(r.get("type")),
                salary_text=(r.get("salary") or "").strip(),
            )
        )
    return jobs


def fetch_jooble_jobs(keywords=None, location=None, part_time=False, pages=2):
    """Jooble aggregates many boards. Key: request one at jooble.org/api/about."""
    if not jooble_configured():
        raise SourceError("Jooble key missing: set JOOBLE_API_KEY in .env")
    url = JOOBLE_API_URL.format(host=os.getenv("JOOBLE_HOST", "au.jooble.org"), key=os.getenv("JOOBLE_API_KEY"))
    words = list(keywords or [])
    if part_time:
        words.append("part time")
    body = {"keywords": " ".join(words)}
    if location and location.lower() != "remote":
        body["location"] = location

    jobs = []
    for page in range(1, pages + 1):
        try:
            response = requests.post(url, json={**body, "page": str(page)},
                                     headers={"User-Agent": USER_AGENT}, timeout=15)
            response.raise_for_status()
            batch = parse_jooble_results(response.json())
        except (requests.RequestException, ValueError) as e:
            if jobs:
                break
            raise SourceError(f"Could not fetch Jooble jobs: {e}") from e
        jobs.extend(batch)
        if not batch:
            break
    return jobs


# --- Company careers pages (via their ATS) -------------------------------

def companies_file():
    return os.getenv("COMPANIES_FILE", "companies.json")


def fetch_company_jobs(keywords=None, location=None, part_time=False):
    from job_agent.ats import fetch_companies, load_companies
    try:
        companies = load_companies(companies_file())
    except (OSError, ValueError) as e:
        raise SourceError(f"Could not read {companies_file()}: {e}") from e
    jobs, errors = fetch_companies(companies)
    for err in errors:
        log.warn(f"Company feed failed: {err}")
    if not jobs and errors:
        raise SourceError("No company careers feeds could be fetched.")
    return jobs


SOURCES = {
    "companies": fetch_company_jobs,
    "adzuna": fetch_adzuna_jobs,
    "jooble": fetch_jooble_jobs,
    "remoteok": fetch_remoteok_jobs,
}


def default_sources():
    names = []
    if os.path.exists(companies_file()):
        names.append("companies")
    if adzuna_configured():
        names.append("adzuna")
    if jooble_configured():
        names.append("jooble")
    names.append("remoteok")
    return names
