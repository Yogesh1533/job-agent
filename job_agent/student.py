"""Part-time / student-visa checks.

For each job: work out the employment type, estimate weekly hours, spot work-rights
requirements a student visa can't meet, and check the hours against the visa cap
(48 hours a fortnight in term, minus hours already committed to other jobs).

These are text heuristics, not legal advice. Always read the ad and check your
visa conditions (e.g. with a registered migration agent).
"""
import os
import re
from dataclasses import dataclass, field

from job_agent.utils import strip_html

HOURS_PER_DAY = 7.6     # standard Australian full-time day (38 h / 5)
FULL_TIME_HOURS = 38.0

# Requirements a student visa holder can't meet.
BLOCKERS = [
    (r"australian citizen", "requires Australian citizenship"),
    (r"permanent residen", "requires permanent residency"),
    (r"\b(baseline|nv1|nv2|negative vetting|agsva|security)\s+clearance", "requires a security clearance"),
    (r"\bnv[12]\b", "requires a security clearance"),
    (r"\b(full|unrestricted|unlimited)\s+(australian\s+)?work(ing)?\s+rights", "requires full work rights"),
    (r"work(ing)?\s+rights\s+without\s+restriction", "requires full work rights"),
]

# Signals that an employer is open to students / flexible hours.
POSITIVES = [
    (r"\bstudents?\b", "students"),
    (r"\buni(versity)?\b", "uni"),
    (r"\bintern(ship)?s?\b", "internship"),
    (r"\bgraduates?\b", "graduate"),
    (r"flexible\s+(hours|roster|schedule|working)", "flexible hours"),
    (r"\bcasual\b", "casual"),
    (r"\bweekends?\b", "weekends"),
    (r"around\s+(your\s+)?stud(y|ies)", "works around study"),
    (r"student\s+visa", "student visa mentioned"),
]

TYPE_PATTERNS = [
    ("internship", r"\bintern(ship)?\b"),
    ("casual", r"\bcasual\b"),
    ("part_time", r"\bpart[\s-]?time\b"),
    ("full_time", r"\bfull[\s-]?time\b"),
]

NUM = r"(\d+(?:\.\d+)?)"
RANGE = rf"{NUM}(?:\s*(?:-|–|to)\s*{NUM})?"
PER_WEEK = r"(?:per|a|each|/)\s*(?:week|wk)"
HOURS_PATTERNS = [
    # "15-20 hours per week", "20 hrs/week", "20h a week"
    (rf"{RANGE}\s*(?:hours?|hrs?|h)\s*{PER_WEEK}", 1.0),
    # "40 hours per fortnight"
    (rf"{RANGE}\s*(?:hours?|hrs?|h)\s*(?:per|a|each|/)\s*fortnight", 0.5),
    # "3 days per week", "2-3 days a week"
    (rf"{RANGE}\s*days?\s*{PER_WEEK}", HOURS_PER_DAY),
]
FTE_PATTERN = r"\b(0?\.\d+|1\.0)\s*fte\b"


@dataclass
class StudentCheck:
    employment_type: str = ""
    hours_low: float = None      # estimated hours per week
    hours_high: float = None
    blockers: list = field(default_factory=list)
    positives: list = field(default_factory=list)
    verdict: str = ""            # fits | partly | over_cap | unknown_hours | blocked | no_cap
    note: str = ""

    @property
    def is_part_time_friendly(self):
        if self.employment_type in ("part_time", "casual", "internship"):
            return True
        if self.employment_type:  # full_time / contract
            return False
        return bool({"casual", "flexible hours", "students", "works around study"} & set(self.positives))


def detect_type(job, text):
    if job.employment_type:
        return job.employment_type
    for name, pattern in TYPE_PATTERNS:
        if re.search(pattern, text):
            return name
    return ""


def estimate_hours(text):
    """Returns (low, high) hours per week, or (None, None)."""
    for pattern, factor in HOURS_PATTERNS:
        m = re.search(pattern, text)
        if m:
            low = float(m.group(1)) * factor
            high = float(m.group(2)) * factor if m.group(2) else low
            if 0 < low <= 80:
                return round(min(low, high), 1), round(max(low, high), 1)
    m = re.search(FTE_PATTERN, text)
    if m:
        h = round(float(m.group(1)) * FULL_TIME_HOURS, 1)
        return h, h
    return None, None


def available_fortnight_hours(term_break=False):
    """Hours a fortnight you can still work. None means no cap (scheduled course break)."""
    if term_break:
        return None
    cap = float(os.getenv("VISA_FORTNIGHT_CAP", "48"))
    committed = float(os.getenv("COMMITTED_FORTNIGHT_HOURS", "0"))
    return max(0.0, cap - committed)


def check_job(job, available=None):
    text = " ".join([job.title or "", " ".join(job.tags or []), strip_html(job.description)]).lower()
    c = StudentCheck()
    c.employment_type = detect_type(job, text)
    c.hours_low, c.hours_high = estimate_hours(text)
    if c.hours_low is None and c.employment_type == "full_time":
        c.hours_low = c.hours_high = FULL_TIME_HOURS
    c.blockers = sorted({reason for pattern, reason in BLOCKERS if re.search(pattern, text)})
    c.positives = sorted({label for pattern, label in POSITIVES if re.search(pattern, text)})

    if c.blockers:
        c.verdict, c.note = "blocked", "; ".join(c.blockers)
    elif available is None:
        c.verdict, c.note = "no_cap", "course break: no hours cap"
    elif c.hours_low is None:
        c.verdict, c.note = "unknown_hours", f"hours not stated; you have {available:g} h/fortnight free"
    else:
        need_low, need_high = c.hours_low * 2, c.hours_high * 2
        hours = f"{c.hours_low:g}" if c.hours_low == c.hours_high else f"{c.hours_low:g}-{c.hours_high:g}"
        if need_high <= available:
            c.verdict = "fits"
        elif need_low <= available:
            c.verdict = "partly"
        else:
            c.verdict = "over_cap"
        c.note = f"~{hours} h/week vs {available:g} h/fortnight free"
    return c


VERDICT_RANK = {"fits": 0, "no_cap": 0, "partly": 1, "unknown_hours": 2, "over_cap": 3, "blocked": 4}
VERDICT_LABEL = {
    "fits": "fits your hours",
    "no_cap": "OK (break)",
    "partly": "fits only at the low end",
    "unknown_hours": "check hours",
    "over_cap": "over your hours cap",
    "blocked": "not open to student visas",
}


def sort_key(job):
    c = job.student
    return (VERDICT_RANK.get(c.verdict, 9), -(job.score if job.score is not None else -1), -len(c.positives))
