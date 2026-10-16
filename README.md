# job-agent

An automated agent for finding job postings and assisting with applications.

It pulls Australian jobs from [Adzuna](https://www.adzuna.com.au) and remote jobs from
[RemoteOK](https://remoteok.com), filters them, scores each one against your resume
with Claude, drafts cover notes for the best matches, and remembers what you've seen.
It does not submit applications.

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
python3 -m job_agent.main --keywords "ai,cyber security,python" --location Sydney --resume resume.txt --output results.md
```

What a run does:

1. **Fetches** jobs from Adzuna Australia (if `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are set; free keys at developer.adzuna.com) and RemoteOK. Pick with `--sources adzuna,remoteok`. If one source fails, the other still runs.
2. **Filters** by whole-word keywords and location. `--location remote` keeps only remote jobs.
3. **Hides** jobs you've already seen (stored in `jobs.db`). `--show-seen` brings them back.
4. **Scores** up to `--max-score` jobs (default 30) against your resume with Claude: a 0-100 score, reasons, and missing skills. Results are sorted best first.
5. **Drafts** cover notes for the top `--draft-top` jobs (default 5).

Results are written as each job is processed, so one failed API call doesn't lose the rest.

Track what you did:

```
python3 -m job_agent.main --mark-applied adzuna:4001
python3 -m job_agent.main --mark-skipped remoteok:123 adzuna:4002
python3 -m job_agent.main --list applied      # seen | applied | skipped | all
```

The Claude model is set by `MODEL` in `.env` (default `claude-sonnet-5-5`).

## Tests

```
python3 -m pytest -q
```

## Status

Week 2 done: Adzuna AU source, AI scoring, SQLite memory.
Next (Week 3): prompt-injection defence, README demo, daily schedule.
