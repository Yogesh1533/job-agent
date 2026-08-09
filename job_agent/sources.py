import requests

from job_agent.models import Job

REMOTEOK_API_URL = "https://remoteok.com/api"


def fetch_remoteok_jobs():
    headers = {"User-Agent": "job-agent/0.1 (personal job search tool)"}
    response = requests.get(REMOTEOK_API_URL, headers=headers, timeout=10)
    response.raise_for_status()
    entries = response.json()

    jobs = []
    for entry in entries:
        if "id" not in entry:
            continue
        jobs.append(
            Job(
                id=str(entry.get("id")),
                # RemoteOK entries may carry explicit null values, and dict.get()
                # only substitutes the default when the key is absent, not when
                # it is present-but-null. Coalesce with `or` so downstream string
                # ops (matcher's join/lower) never see None.
                title=entry.get("position") or "",
                company=entry.get("company") or "",
                location=entry.get("location") or "Remote",
                url=entry.get("url") or "",
                description=entry.get("description") or "",
                tags=entry.get("tags") or [],
            )
        )
    return jobs
