from job_agent.utils import strip_html


def filter_jobs(jobs, keywords=None, location=None):
    keywords = [k.lower() for k in (keywords or [])]
    location = location.lower() if location else None

    matched = []
    for job in jobs:
        haystack = " ".join(
            [job.title, job.company, " ".join(job.tags), strip_html(job.description)]
        ).lower()

        if keywords and not any(k in haystack for k in keywords):
            continue
        if location and location not in job.location.lower() and location != "remote":
            continue

        matched.append(job)

    return matched
