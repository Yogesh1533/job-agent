from unittest.mock import MagicMock, patch

import pytest
import requests

from job_agent.sources import fetch_remoteok_jobs


def _mock_response(json_data, status=200):
    resp = MagicMock()
    resp.json.return_value = json_data
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    else:
        resp.raise_for_status.return_value = None
    return resp


@patch("job_agent.sources.requests.get")
def test_parses_entries_into_jobs(mock_get, ):
    mock_get.return_value = _mock_response(
        [
            {
                "id": 42,
                "position": "Backend Engineer",
                "company": "Acme",
                "location": "Worldwide",
                "url": "https://remoteok.com/l/42",
                "description": "<p>Do backend things</p>",
                "tags": ["python", "aws"],
            }
        ]
    )
    jobs = fetch_remoteok_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.id == "42"  # coerced to string
    assert job.title == "Backend Engineer"
    assert job.company == "Acme"
    assert job.location == "Worldwide"
    assert job.tags == ["python", "aws"]


@patch("job_agent.sources.requests.get")
def test_entries_without_id_are_skipped(mock_get):
    # RemoteOK's feed begins with a legal/metadata object that has no "id".
    mock_get.return_value = _mock_response(
        [
            {"legal": "RemoteOK terms..."},
            {"id": 7, "position": "Dev"},
        ]
    )
    jobs = fetch_remoteok_jobs()
    assert [j.id for j in jobs] == ["7"]


@patch("job_agent.sources.requests.get")
def test_missing_fields_get_defaults(mock_get):
    mock_get.return_value = _mock_response([{"id": 1}])
    job = fetch_remoteok_jobs()[0]
    assert job.title == ""
    assert job.company == ""
    assert job.location == "Remote"
    assert job.url == ""
    assert job.description == ""
    assert job.tags == []


@patch("job_agent.sources.requests.get")
def test_explicit_null_fields_are_coalesced(mock_get):
    # Regression: dict.get(key, default) returns None when the key is present
    # but null, which previously crashed the matcher on join()/lower().
    mock_get.return_value = _mock_response(
        [
            {
                "id": 1,
                "position": None,
                "company": None,
                "location": None,
                "url": None,
                "description": None,
                "tags": None,
            }
        ]
    )
    job = fetch_remoteok_jobs()[0]
    assert job.title == ""
    assert job.company == ""
    assert job.location == "Remote"
    assert job.url == ""
    assert job.description == ""
    assert job.tags == []


@patch("job_agent.sources.requests.get")
def test_http_error_propagates(mock_get):
    mock_get.return_value = _mock_response([], status=500)
    with pytest.raises(requests.HTTPError):
        fetch_remoteok_jobs()


@patch("job_agent.sources.requests.get")
def test_request_uses_custom_user_agent_and_timeout(mock_get):
    mock_get.return_value = _mock_response([])
    fetch_remoteok_jobs()
    _, kwargs = mock_get.call_args
    assert "User-Agent" in kwargs["headers"]
    assert kwargs["timeout"] == 10
