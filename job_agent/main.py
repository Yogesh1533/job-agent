import argparse
import os
import sys

from dotenv import load_dotenv

from job_agent import sources as sources_mod
from job_agent.drafter import draft_application
from job_agent.matcher import filter_jobs
from job_agent.scorer import score_job, sort_by_score
from job_agent.sources import SourceError
from job_agent.store import JobStore

SEPARATOR = "\n\n---\n\n"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Find jobs, score them against your resume, and draft cover notes.")
    p.add_argument("--keywords", help="Comma-separated keywords (e.g. 'python,cyber security,ai')")
    p.add_argument("--location", help="Location filter (e.g. 'Sydney' or 'remote')")
    p.add_argument("--resume", help="Plain-text resume. Enables AI scoring and cover notes.")
    p.add_argument("--sources", help="Comma-separated: adzuna,remoteok (default: adzuna if keys are set, plus remoteok)")
    p.add_argument("--limit", type=int, default=10, help="Max jobs to show (default 10)")
    p.add_argument("--max-score", type=int, default=30, help="Max jobs to send to Claude for scoring (default 30)")
    p.add_argument("--draft-top", type=int, default=5, help="Draft cover notes for the top N scored jobs (default 5)")
    p.add_argument("--output", help="Write results as Markdown (default: stdout)")
    p.add_argument("--db", default="jobs.db", help="SQLite file for seen/applied/skipped jobs (default jobs.db)")
    p.add_argument("--show-seen", action="store_true", help="Include jobs you've already seen")
    p.add_argument("--mark-applied", nargs="+", metavar="JOB_ID", help="Mark job(s) as applied, then exit")
    p.add_argument("--mark-skipped", nargs="+", metavar="JOB_ID", help="Mark job(s) as skipped, then exit")
    p.add_argument("--list", choices=["seen", "applied", "skipped", "all"], help="List remembered jobs, then exit")
    return p.parse_args(argv)


def format_job(job, draft=None, error=None):
    header = f"## {job.title} — {job.company}"
    if job.score is not None:
        header = f"## [{job.score}] {job.title} — {job.company}"
    lines = [
        header,
        f"- ID: `{job.id}`",
        f"- Location: {job.location}",
        f"- URL: {job.url}",
    ]
    if job.reasons:
        lines.append("- Why: " + "; ".join(job.reasons))
    if job.missing_skills:
        lines.append("- Missing: " + ", ".join(job.missing_skills))
    if draft:
        lines.append(f"\n**Draft cover note:**\n\n{draft}")
    elif error:
        lines.append(f"\n_{error}_")
    return "\n".join(lines)


def warn(msg):
    print(msg, file=sys.stderr)


def fetch_all(source_names, keywords, location):
    jobs, failures = [], []
    for name in source_names:
        fetch = sources_mod.SOURCES.get(name)
        if not fetch:
            failures.append(f"Unknown source: {name}")
            continue
        try:
            jobs.extend(fetch(keywords=keywords, location=location))
        except SourceError as e:
            failures.append(str(e))
    for f in failures:
        warn(f)
    if not jobs and failures:
        raise SourceError("No jobs fetched from any source.")
    return dedupe(jobs)


def dedupe(jobs):
    seen_ids, seen_keys, out = set(), set(), []
    for job in jobs:
        key = (job.title.strip().lower(), job.company.strip().lower())
        if job.id in seen_ids or (key[0] and key in seen_keys):
            continue
        seen_ids.add(job.id)
        seen_keys.add(key)
        out.append(job)
    return out


def handle_store_commands(args, store):
    """Returns True if a store-only command ran (and the program should exit)."""
    ran = False
    for status, ids in (("applied", args.mark_applied), ("skipped", args.mark_skipped)):
        for job_id in ids or []:
            ran = True
            if store.set_status(job_id, status):
                print(f"Marked {job_id} as {status}")
            else:
                warn(f"Unknown job id: {job_id} (run a search first so it's remembered)")
    if args.list:
        ran = True
        rows = store.list(None if args.list == "all" else args.list)
        if not rows:
            print("Nothing here yet.")
        for r in rows:
            score = f"[{r['score']}] " if r["score"] is not None else ""
            print(f"{r['status']:8} {score}{r['title']} — {r['company']}  ({r['id']})")
    return ran


def main(argv=None):
    load_dotenv()
    args = parse_args(argv)
    store = JobStore(args.db)
    try:
        if handle_store_commands(args, store):
            return
        run_search(args, store)
    finally:
        store.close()


def run_search(args, store):
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None
    source_names = (
        [s.strip().lower() for s in args.sources.split(",") if s.strip()]
        if args.sources else sources_mod.default_sources()
    )

    resume_text = None
    if args.resume:
        if not os.path.exists(args.resume):
            warn(f"Resume file not found: {args.resume}")
            sys.exit(1)
        with open(args.resume) as f:
            resume_text = f.read()

    try:
        jobs = fetch_all(source_names, keywords, args.location)
    except SourceError as e:
        warn(str(e))
        sys.exit(1)

    matched = filter_jobs(jobs, keywords=keywords, location=args.location)
    if not args.show_seen:
        known = store.known_ids()
        hidden = sum(1 for j in matched if j.id in known)
        matched = [j for j in matched if j.id not in known]
        if hidden:
            warn(f"Hid {hidden} job(s) you've already seen (use --show-seen to include them).")

    if not matched:
        print("No new matching jobs found.")
        return

    if resume_text:
        to_score = matched[: args.max_score]
        for job in to_score:
            try:
                score_job(job, resume_text)
            except Exception as e:  # unscored jobs sink to the bottom
                warn(f"Scoring failed for '{job.title}': {type(e).__name__}: {e}")
        matched = sort_by_score(to_score)

    matched = matched[: args.limit]

    out = open(args.output, "w") if args.output else sys.stdout
    drafted = 0
    try:
        for i, job in enumerate(matched):
            draft, error = None, None
            if resume_text and job.score is not None and drafted < args.draft_top:
                drafted += 1
                try:
                    draft = draft_application(job, resume_text)
                except Exception as e:  # keep going; one bad call shouldn't lose the rest
                    error = f"Draft failed: {type(e).__name__}: {e}"
                    warn(f"Draft failed for '{job.title}': {e}")
            if i:
                out.write(SEPARATOR)
            out.write(format_job(job, draft, error))
            out.flush()  # each result is saved as soon as it's produced
            store.mark_seen(job)
        out.write("\n")
    finally:
        if out is not sys.stdout:
            out.close()

    if args.output:
        print(f"Wrote {len(matched)} job(s) to {args.output}")


if __name__ == "__main__":
    main()
