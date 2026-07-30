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
                title=entry.get("position", ""),
                company=entry.get("company", ""),
                location=entry.get("location", "Remote"),
                url=entry.get("url", ""),
                description=entry.get("description", ""),
                tags=entry.get("tags", []),
            )
        )
    return jobs
