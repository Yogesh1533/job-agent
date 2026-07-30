# job-agent

An automated agent for finding job postings and assisting with applications.

It searches [RemoteOK](https://remoteok.com)'s public job feed, filters listings
by keyword/location, and — if you provide a resume — uses Claude to draft a
tailored cover note for each matching job. It does not submit applications.

## Setup

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your ANTHROPIC_API_KEY
cp resume.example.txt resume.txt   # replace with your real resume
```

## Usage

```
python3 -m job_agent.main --keywords "python,backend" --location remote --limit 10
```

Add `--resume resume.txt` to also generate an AI-drafted cover note per job.
Add `--output results.md` to write results to a file instead of stdout.

## Status

MVP: search + filter + AI-assisted cover note drafting. No automated
application submission.
