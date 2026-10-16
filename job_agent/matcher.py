import re

from job_agent.utils import strip_html

# Words in a job's location or tags that mean it can be done remotely.
REMOTE_MARKERS = ("remote", "anywhere", "worldwide", "work from home", "wfh")


def keyword_pattern(keyword):
    """Whole-word, case-insensitive pattern, so 'ai' does not match 'email'."""
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", re.IGNORECASE)


def is_remote(job):
    text = " ".join([job.location or "", " ".join(job.tags or [])]).lower()
    return any(marker in text for marker in REMOTE_MARKERS)


def filter_jobs(jobs, keywords=None, location=None):
    patterns = [keyword_pattern(k) for k in (keywords or []) if k.strip()]
    location = location.strip().lower() if location else None

    matched = []
    for job in jobs:
        haystack = " ".join(
            [
                job.title or "",
                job.company or "",
                " ".join(job.tags or []),
                strip_html(job.description),
            ]
        )

        if patterns and not any(p.search(haystack) for p in patterns):
            continue

        if location == "remote":
            if not is_remote(job):
                continue
        elif location and location not in (job.location or "").lower():
            continue

        matched.append(job)

    return matched
