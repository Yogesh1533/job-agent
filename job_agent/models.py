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
    # Filled in by the scorer
    score: int = None
    reasons: list = field(default_factory=list)
    missing_skills: list = field(default_factory=list)
