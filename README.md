# job-agent

An automated agent for finding job postings and assisting with applications.

It pulls jobs from four kinds of source:

| Source | What it is | Setup |
|---|---|---|
| `companies` | Straight from company careers pages (Greenhouse, Lever, Ashby, Workable, SmartRecruiters feeds) | `companies.json` |
| `adzuna` | Australian aggregator | free keys |
| `jooble` | Aggregator | key by request (optional) |
| `remoteok` | Remote jobs | none |

Then it filters them, scores each one against your resume with Claude, drafts cover
notes for the best matches, and remembers what you've seen. A `--part-time` student
mode checks each job against your student-visa hours cap. It does not submit applications.

Seek, LinkedIn and Indeed have no public job-search API and their terms forbid
scraping, so they aren't direct sources. Adzuna and Jooble pick up many of the same ads,
and the `companies` source goes to the employer directly.

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

### Company careers pages

`companies.json` lists employers whose careers feeds are read directly. To add one:

```
python3 -m job_agent.main --discover "Deputy"
```

It tries Greenhouse, Lever, Ashby, Workable and SmartRecruiters with likely names and
adds the one with the most open jobs. Companies on Workday or a custom site can't be
added this way.

### Part-time student mode

```
python3 -m job_agent.main --part-time --keywords "it support,helpdesk,cyber,ai" --location Sydney --resume resume.txt
```

- Keeps only part-time, casual and internship roles (or ads that say students/flexible hours).
- Drops ads needing citizenship, PR, a security clearance or full work rights (`--show-blocked` to see them).
- Estimates weekly hours from the ad ("15-20 hours per week", "3 days a week", "0.6 FTE")
  and compares them with your free hours: `VISA_FORTNIGHT_CAP` minus `COMMITTED_FORTNIGHT_HOURS` in `.env`.
- Each job is labelled: fits your hours / fits only at the low end / check hours / over your hours cap.
- `--fits-only` hides everything that doesn't clearly fit. `--term-break` turns the cap off for scheduled course breaks.

These checks read the ad's text, so they can miss things. They're not legal advice:
check your visa conditions with a registered migration agent.

### Track what you did

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
Added: company careers-page feeds, Jooble, part-time student/visa mode.
Next (Week 3): prompt-injection defence, README demo, daily schedule.
