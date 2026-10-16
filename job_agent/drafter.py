from job_agent import llm
from job_agent.utils import strip_html

PROMPT_TEMPLATE = """You are helping a job seeker draft a short, tailored cover note for a job application.

Resume:
{resume}

Job title: {title}
Company: {company}
Job description:
{description}

Write a concise (under 200 words) cover note that connects the resume's relevant \
experience to this specific role. Be specific, not generic. Do not invent \
experience that isn't in the resume."""


def draft_application(job, resume_text, generate=None):
    generate = generate or llm.generate

    prompt = PROMPT_TEMPLATE.format(
        resume=resume_text,
        title=job.title,
        company=job.company,
        description=strip_html(job.description)[:4000],
    )
    return generate(prompt, max_tokens=500).strip()
