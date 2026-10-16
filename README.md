# job-agent

An automated agent for finding job postings and assisting with applications.

It pulls jobs from four kinds of source:

| Source | What it is | Setup |
|---|---|---|
| `companies` | Straight from company careers pages (Greenhouse, Lever, Ashby, Workable, SmartRecruiters feeds) | `companies.json` |
| `adzuna` | Australian aggregator | free keys |
| `jooble` | Aggregator | key by request (optional) |
| `remoteok` | Remote jobs | none |

Then it filters them, scores each one against your resume with AI, drafts cover
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
cp .env.example .env   # add your GEMINI_API_KEY (free: aistudio.google.com/apikey)
cp resume.example.txt resume.txt   # replace with your real resume
```

## Dashboard (web app)

```
python3 -m job_agent.web
```

This opens http://127.0.0.1:8765 in your browser. It runs only on your computer and costs nothing (no hosting, no paid services). Leave the terminal open while you use it, and press Ctrl+C to stop it.

- **Dashboard:** new jobs to review, strong matches, what you've applied for, the last search and the next scheduled one.
- **Jobs:** every job found, with filters (status, hours check, score, remote/hybrid/on-site, paid/unpaid, job type) and search. Click a job to see why it fits, missing skills, the visa hours check, and its documents.
- **Cover letter and tailored resume:** written by the AI for each job, following humanizer rules (no dashes, no stock AI phrases) and using only facts from `resume.txt`. A tailored resume flags any number or skill that isn't in your real resume, so you can check it before sending. Copy or download as .txt.
- **Runs:** history of every search (from the dashboard, the schedule or the command line), with counts and the log.
- **Settings:** keywords, location, sources, kind of job, student visa mode, AI limits and a daily schedule. The schedule runs while the dashboard is open, and catches up when you next open it if it was missed.

API keys stay in `.env` and are never shown in the page. The app refuses requests from other websites, and everything from job ads is shown as plain text.

## Usage

```
python3 -m job_agent.main --keywords "ai,cyber security,python" --location Sydney --resume resume.txt --output results.md
```

What a run does:

1. **Fetches** jobs from Adzuna Australia (if `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` are set; free keys at developer.adzuna.com) and RemoteOK. Pick with `--sources adzuna,remoteok`. If one source fails, the other still runs.
2. **Filters** by whole-word keywords and location. `--location remote` keeps only remote jobs.
3. **Hides** jobs you've already seen (stored in `jobs.db`). `--show-seen` brings them back.
4. **Scores** up to `--max-score` jobs (default 30) against your resume with AI: a 0-100 score, reasons, and missing skills. Results are sorted best first.
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

### Kind of job

```
python3 -m job_agent.main --work-mode remote,hybrid --pay paid --job-type casual,part_time
```

`--work-mode` takes remote, hybrid, onsite or unknown. `--pay` takes paid, unpaid or unknown. `--job-type` takes full_time, part_time, casual, internship, contract or unknown. "unknown" keeps ads that don't say. These are read from the job feed first and the ad's text second, so check the ad.

### Track what you did

```
python3 -m job_agent.main --mark-applied adzuna:4001
python3 -m job_agent.main --mark-skipped remoteok:123 adzuna:4002
python3 -m job_agent.main --list applied      # seen | applied | skipped | all
```

### AI provider

Scoring and cover notes use the **Gemini API** (a free key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) works).
Set `GEMINI_API_KEY` in `.env`; the model defaults to `gemini-3.5-flash-lite` (`GEMINI_MODEL` to change it).

- Free keys have per-minute and per-day limits. Calls are spaced out (`GEMINI_MIN_INTERVAL`, default 4 s) and retried when rate limited.
  If 3 calls in a row fail (e.g. the free quota ran out), it stops AI scoring and still lists the jobs. Lower `--max-score` to use fewer requests.
- No key set? The job search still runs, just without scores or cover notes.
- Free-tier prompts may be used by Google to improve its products, so leave your phone number and home address out of `resume.txt`.
- To use Claude instead: `pip install anthropic`, then set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

## Tests

```
python3 -m pytest -q
```

## Status

Week 2 done: Adzuna AU source, AI scoring, SQLite memory.
Added: company careers-page feeds, Jooble, part-time student/visa mode.
Next (Week 3): prompt-injection defence, README demo, daily schedule.
