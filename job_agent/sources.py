import requests

from job_agent.models import Job

REMOTEOK_API_URL = "https://remoteok.com/api"


class SourceError(Exception):
    """Raised when a job source can't be fetched or parsed."""


def parse_remoteok_entries(entries):
    jobs = []
    for entry in entries or []:
        if not isinstance(entry, dict) or "id" not in entry:
            continue  # first item in the RemoteOK feed is a legal notice
        jobs.append(
            Job(
                id=str(entry.get("id")),
                title=entry.get("position") or "",
                company=entry.get("company") or "",
                location=entry.get("location") or "Remote",
                url=entry.get("url") or "",
                description=entry.get("description") or "",
                tags=[t for t in (entry.get("tags") or []) if t],
            )
        )
    return jobs


def fetch_remoteok_jobs():
    headers = {"User-Agent": "job-agent/0.1 (personal job search tool)"}
    try:
        response = requests.get(REMOTEOK_API_URL, headers=headers, timeout=10)
        response.raise_for_status()
        entries = response.json()
    except (requests.RequestException, ValueError) as e:
        raise SourceError(f"Could not fetch RemoteOK jobs: {e}") from e
    return parse_remoteok_entries(entries)
