from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    tags: list
    source: str = ""
    # "full_time", "part_time", "casual", "internship", "contract" or "" if unknown
    employment_type: str = ""
    # "remote", "hybrid", "onsite" or "" if the ad doesn't say
    work_mode: str = ""
    # "paid", "unpaid" or "" if unknown
    pay: str = ""
    salary_text: str = ""
    category: str = ""
    # Filled in by the scorer
    score: int = None
    reasons: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)
    # Filled in by student.check_job in --part-time mode
    student: object = None
