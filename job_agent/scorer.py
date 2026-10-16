"""Score each job against the resume with Claude.

Claude returns JSON: {"score": 0-100, "reasons": [...], "missing_skills": [...]}.
(This is the structured-JSON stage the plan called tailor.py.)
"""
import json
import re

import anthropic

from job_agent.drafter import get_model
from job_agent.utils import strip_html

SCORE_PROMPT = """You are screening a job posting for a job seeker. Compare the resume to the job and rate the fit.

Resume:
{resume}

Job title: {title}
Company: {company}
Location: {location}
Job description:
{description}

Reply with ONLY a JSON object, no other text:
{{"score": <integer 0-100, how well the resume fits this job>,
  "reasons": [<2-3 short reasons for the score>],
  "missing_skills": [<skills or requirements the job asks for that the resume lacks; empty list if none>]}}"""


class ScoreError(Exception):
    pass


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_score(text):
    """Pull the JSON object out of Claude's reply and validate it."""
    match = _JSON_RE.search(text or "")
    if not match:
        raise ScoreError(f"No JSON in reply: {text[:100]!r}")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ScoreError(f"Bad JSON: {e}") from e

    try:
        score = int(round(float(data.get("score"))))
    except (TypeError, ValueError) as e:
        raise ScoreError(f"Missing or non-numeric score: {data.get('score')!r}") from e
    score = max(0, min(100, score))

    def str_list(value):
        if isinstance(value, str):
            value = [value]
        return [str(v).strip() for v in (value or []) if str(v).strip()]

    return {
        "score": score,
        "reasons": str_list(data.get("reasons")),
        "missing_skills": str_list(data.get("missing_skills")),
    }


def score_job(job, resume_text, client=None):
    client = client or anthropic.Anthropic()
    prompt = SCORE_PROMPT.format(
        resume=resume_text,
        title=job.title,
        company=job.company,
        location=job.location,
        description=strip_html(job.description)[:4000],
    )
    response = client.messages.create(
        model=get_model(),
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    result = parse_score(response.content[0].text)
    job.score = result["score"]
    job.reasons = result["reasons"]
    job.missing_skills = result["missing_skills"]
    return result


def sort_by_score(jobs):
    """Highest score first; unscored jobs go last."""
    return sorted(jobs, key=lambda j: -1 if j.score is None else j.score, reverse=True)
