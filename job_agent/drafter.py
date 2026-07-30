import anthropic

from job_agent.utils import strip_html

MODEL = "claude-sonnet-5"

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


def draft_application(job, resume_text, client=None):
    client = client or anthropic.Anthropic()

    prompt = PROMPT_TEMPLATE.format(
        resume=resume_text,
        title=job.title,
        company=job.company,
        description=strip_html(job.description)[:4000],
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
