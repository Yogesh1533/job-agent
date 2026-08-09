import pytest

from job_agent.models import Job


@pytest.fixture
def make_job():
    """Factory for Job instances with sensible defaults."""

    def _make(
        id="1",
        title="Senior Python Engineer",
        company="Acme",
        location="Remote",
        url="https://example.com/jobs/1",
        description="Build backend services in Python.",
        tags=None,
    ):
        return Job(
            id=id,
            title=title,
            company=company,
            location=location,
            url=url,
            description=description,
            tags=tags if tags is not None else ["python", "backend"],
        )

    return _make
