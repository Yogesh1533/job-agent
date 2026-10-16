"""Kept for compatibility: a run's 'draft' is now a full humanized cover letter."""
from job_agent import writer


def draft_application(job, resume_text, generate=None):
    return writer.cover_letter(job, resume_text, generate=generate)
