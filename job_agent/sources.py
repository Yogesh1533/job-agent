import os

import requests

from job_agent.models import Job

REMOTEOK_API_URL = "https://remoteok.com/api"
ADZUNA_API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
USER_AGENT = "job-agent/0.2 (personal job search tool)"


class SourceError(Exception):
    """Raised when a job source can't be fetched or parsed."""


# --- RemoteOK -----------------------------------------------------------

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
            )
        )
    return jobs


def fetch_remoteok_jobs(keywords=None, location=None):
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
            )
        )
    return jobs


def fetch_adzuna_jobs(keywords=None, location=None, pages=2, per_page=50):
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


SOURCES = {
    "remoteok": fetch_remoteok_jobs,
    "adzuna": fetch_adzuna_jobs,
}


def default_sources():
    return ["adzuna", "remoteok"] if adzuna_configured() else ["remoteok"]
