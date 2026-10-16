"""Work out a job's work mode (remote/hybrid/on-site), pay (paid/unpaid) and type,
from what the feed says first and the ad's text second. Unknown stays unknown."""
import re

from job_agent import log
from job_agent.utils import strip_html

WORK_MODES = ("remote", "hybrid", "onsite", "unknown")
PAY_OPTIONS = ("paid", "unpaid", "unknown")
JOB_TYPES = ("full_time", "part_time", "casual", "internship", "contract", "unknown")
EMPLOYMENT_TYPES = {"full_time", "part_time", "casual", "contract"}  # employees must be paid in Australia

HYBRID = re.compile(r"\bhybrid\b")
# "remote support" is an IT skill, not a work mode, so only clear phrases count
REMOTE = re.compile(
    r"\b(fully remote|100% remote|remote[- ](first|role|position|job|based|friendly|working)"
    r"|work(ing)? remotely|work(ing)? from home|wfh|anywhere in australia)\b")
ONSITE = re.compile(r"\b(on[- ]?site|in[- ]office|in the office|in[- ]person)\b")
UNPAID = re.compile(r"\b(unpaid|volunteer(s|ing)?|voluntary (role|position)|pro bono|no (pay|salary|remuneration))\b")
PAID = re.compile(
    r"(\$\s?\d|\bper hour\b|\bhourly rate\b|\bsalary\b|\bsuper(annuation)?\b|\bpaid (internship|role|position|work)\b"
    r"|\b(modern )?award rates?\b|\bremuneration\b|\bwages?\b|\bpenalty rates\b)")
DOLLARS = re.compile(r"\$\s?\d")


def normalise_mode(value):
    v = re.sub(r"[^a-z]", "", str(value or "").lower())
    if "hybrid" in v:
        return "hybrid"
    if "remote" in v:
        return "remote"
    if v in ("onsite", "inoffice", "office", "inperson"):
        return "onsite"
    return ""


def fmt_salary(low=None, high=None, currency="$", interval="year"):
    def money(x):
        x = float(x)
        return f"{currency}{x:,.0f}" if x >= 1000 else f"{currency}{x:,.2f}".replace(".00", "")
    try:
        if low and high and float(low) != float(high):
            return f"{money(low)} to {money(high)} a {interval}"
        if low or high:
            return f"{money(low or high)} a {interval}"
    except (TypeError, ValueError):
        pass
    return ""


def _text(job):
    return " ".join([job.title or "", strip_html(job.description)]).lower()


def detect_work_mode(job):
    if job.work_mode:
        return job.work_mode
    location = " ".join([job.location or "", " ".join(job.tags or [])]).lower()
    text = _text(job)
    if HYBRID.search(text) or "hybrid" in location:
        return "hybrid"
    if "remote" in location or REMOTE.search(text):
        return "remote"
    if ONSITE.search(text):
        return "onsite"
    return ""


def detect_pay(job):
    if job.pay:
        return job.pay
    if job.salary_text:
        return "paid"
    text = _text(job)
    if UNPAID.search(text) and not DOLLARS.search(text):
        return "unpaid"
    if PAID.search(text):
        return "paid"
    if job.employment_type in EMPLOYMENT_TYPES:
        return "paid"
    return ""


def classify(job):
    from job_agent.student import detect_type
    job.employment_type = detect_type(job, _text(job))
    job.work_mode = detect_work_mode(job)
    job.pay = detect_pay(job)
    return job


def parse_choice_list(value, allowed):
    """'remote,hybrid' -> ['remote', 'hybrid']; raises ValueError on unknown values."""
    items = [x.strip().lower().replace("-", "_") for x in (value or "").split(",") if x.strip()]
    items = ["onsite" if x in ("on_site", "physical", "office") else x for x in items]
    bad = [x for x in items if x not in allowed]
    if bad:
        raise ValueError(f"unknown value(s): {', '.join(bad)} (choose from {', '.join(allowed)})")
    return items


def apply_filters(jobs, work_modes=None, pays=None, job_types=None):
    """Keep jobs whose values are in each chosen list ('unknown' = not stated). Empty list = any."""
    checks = [("work mode", "work_mode", set(work_modes or [])),
              ("pay", "pay", set(pays or [])),
              ("job type", "employment_type", set(job_types or []))]
    kept = []
    dropped = {name: 0 for name, _, _ in checks}
    for job in jobs:
        for name, attr, allowed in checks:
            if allowed and (getattr(job, attr) or "unknown") not in allowed:
                dropped[name] += 1
                break
        else:
            kept.append(job)
    for name, n in dropped.items():
        if n:
            log.warn(f"Dropped {n} job(s) not matching your {name} filter.")
    return kept
