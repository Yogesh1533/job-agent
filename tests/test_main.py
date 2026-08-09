from unittest.mock import patch

import pytest

import job_agent.main as main_mod
from job_agent.main import format_job, main, parse_args


# ---- format_job ---------------------------------------------------------

def test_format_job_without_draft(make_job):
    out = format_job(make_job(title="Dev", company="Acme", location="Remote", url="u"))
    assert "## Dev — Acme" in out
    assert "Location: Remote" in out
    assert "URL: u" in out
    assert "Draft cover note" not in out


def test_format_job_with_draft(make_job):
    out = format_job(make_job(), draft="Hello there")
    assert "Draft cover note" in out
    assert "Hello there" in out


# ---- parse_args ---------------------------------------------------------

def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog"])
    args = parse_args()
    assert args.limit == 10
    assert args.keywords is None
    assert args.resume is None


# ---- main orchestration -------------------------------------------------

def _run_main(monkeypatch, argv, jobs):
    monkeypatch.setattr("sys.argv", ["prog"] + argv)
    monkeypatch.setattr(main_mod, "fetch_remoteok_jobs", lambda: jobs)


def test_main_no_matches(monkeypatch, capsys):
    _run_main(monkeypatch, ["--keywords", "cobol"], [])
    main()
    assert "No matching jobs found." in capsys.readouterr().out


def test_main_keywords_are_split_and_trimmed(monkeypatch, make_job):
    jobs = [make_job()]
    _run_main(monkeypatch, ["--keywords", "python, backend"], jobs)
    with patch.object(main_mod, "filter_jobs", return_value=jobs) as mock_filter:
        main()
    _, kwargs = mock_filter.call_args
    assert kwargs["keywords"] == ["python", "backend"]


def test_main_respects_limit(monkeypatch, make_job, capsys):
    jobs = [make_job(id=str(i), title=f"Job{i}", tags=[], description="") for i in range(5)]
    _run_main(monkeypatch, ["--keywords", "job", "--limit", "2"], jobs)
    main()
    out = capsys.readouterr().out
    assert out.count("## ") == 2


def test_main_missing_resume_file_exits(monkeypatch, make_job):
    _run_main(monkeypatch, ["--resume", "/nope/missing.txt"], [make_job()])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_main_writes_output_file(monkeypatch, make_job, tmp_path, capsys):
    out_file = tmp_path / "results.md"
    jobs = [make_job(title="Dev", tags=[], description="")]
    _run_main(monkeypatch, ["--keywords", "dev", "--output", str(out_file)], jobs)
    main()
    assert out_file.exists()
    assert "## Dev" in out_file.read_text()
    assert "Wrote 1 job(s)" in capsys.readouterr().out


def test_main_drafts_when_resume_provided(monkeypatch, make_job, tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("my experience")
    jobs = [make_job(title="Dev", tags=[], description="")]
    _run_main(monkeypatch, ["--keywords", "dev", "--resume", str(resume)], jobs)
    with patch.object(main_mod, "draft_application", return_value="DRAFTED") as mock_draft:
        main()
    mock_draft.assert_called_once()
