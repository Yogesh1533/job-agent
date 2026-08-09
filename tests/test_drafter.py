from unittest.mock import MagicMock

from job_agent.drafter import draft_application


def _fake_client(return_text="Dear hiring manager..."):
    client = MagicMock()
    message = MagicMock()
    message.content = [MagicMock(text=return_text)]
    client.messages.create.return_value = message
    return client


def test_returns_text_from_response(make_job):
    client = _fake_client("Tailored note")
    result = draft_application(make_job(), "my resume", client=client)
    assert result == "Tailored note"


def test_prompt_includes_job_and_resume_fields(make_job):
    client = _fake_client()
    job = make_job(title="Data Engineer", company="Globex")
    draft_application(job, "RESUME_MARKER", client=client)

    _, kwargs = client.messages.create.call_args
    prompt = kwargs["messages"][0]["content"]
    assert "Data Engineer" in prompt
    assert "Globex" in prompt
    assert "RESUME_MARKER" in prompt


def test_description_html_is_stripped_and_truncated(make_job):
    client = _fake_client()
    long_desc = "<p>" + ("x" * 5000) + "</p>"
    draft_application(make_job(description=long_desc), "resume", client=client)

    _, kwargs = client.messages.create.call_args
    prompt = kwargs["messages"][0]["content"]
    assert "<p>" not in prompt
    # Description is truncated to 4000 chars, so the full 5000-x run cannot appear.
    assert "x" * 5000 not in prompt
    assert "x" * 4000 in prompt


def test_uses_configured_model(make_job):
    client = _fake_client()
    draft_application(make_job(), "resume", client=client)
    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "claude-sonnet-5"
