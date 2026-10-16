import argparse
import io
import os
import sys

from dotenv import load_dotenv

from job_agent import classify
from job_agent import llm
from job_agent import log
from job_agent import sources as sources_mod
from job_agent.drafter import draft_application
from job_agent.matcher import filter_jobs
from job_agent.scorer import score_job, sort_by_score
from job_agent.sources import SourceError
from job_agent.store import JobStore
from job_agent import student as student_mod
from job_agent import ats

SEPARATOR = "\n\n---\n\n"
MODE_LABEL = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}
PAY_LABEL = {"paid": "Paid", "unpaid": "Unpaid"}
MAX_LLM_FAILURES_IN_A_ROW = 3  # then stop: a bad key or used-up free quota would fail every job


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Find jobs, score them against your resume, and draft cover notes.")
    p.add_argument("--keywords", help="Comma-separated keywords (e.g. 'python,cyber security,ai')")
    p.add_argument("--location", help="Location filter (e.g. 'Sydney' or 'remote')")
    p.add_argument("--resume", help="Plain-text resume. Enables AI scoring and cover notes.")
    p.add_argument("--sources", help="Comma-separated: companies,adzuna,jooble,remoteok (default: every source that's set up)")
    p.add_argument("--companies", default="companies.json", help="Company careers-page list (default companies.json)")
    p.add_argument("--part-time", action="store_true", help="Student mode: part-time/casual/internship only, checked against your visa hours cap")
    p.add_argument("--term-break", action="store_true", help="With --part-time: you're on a scheduled course break, so no hours cap")
    p.add_argument("--fits-only", action="store_true", help="With --part-time: only show jobs that fit your free hours")
    p.add_argument("--show-blocked", action="store_true", help="With --part-time: include jobs needing citizenship/PR/clearance/full work rights")
    p.add_argument("--work-mode", type=_choices(classify.WORK_MODES), default=[],
                   help="Only these work modes: remote,hybrid,onsite,unknown (unknown = ad doesn't say)")
    p.add_argument("--pay", type=_choices(classify.PAY_OPTIONS), default=[],
                   help="Only paid, unpaid and/or unknown, e.g. --pay paid")
    p.add_argument("--job-type", type=_choices(classify.JOB_TYPES), default=[],
                   help="Only these types: full_time,part_time,casual,internship,contract,unknown")
    p.add_argument("--discover", metavar="COMPANY", help="Find a company's careers-page feed and add it to the company list, then exit")
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
    details = [MODE_LABEL.get(job.work_mode, ""), PAY_LABEL.get(job.pay, ""),
               (job.employment_type or "").replace("_", "-"), job.salary_text, job.category]
    details = [d for d in details if d]
    if details:
        lines.append("- Details: " + " · ".join(details))
    if job.reasons:
        lines.append("- Why: " + "; ".join(job.reasons))
    if job.missing_skills:
        lines.append("- Missing: " + ", ".join(job.missing_skills))
    c = job.student
    if c is not None:
        kind = (c.employment_type or "type not stated").replace("_", "-")
        lines.append(f"- Student check: {student_mod.VERDICT_LABEL.get(c.verdict, c.verdict)} ({c.note}) · {kind}")
        if c.positives:
            lines.append("- Student-friendly signs: " + ", ".join(c.positives))
    if draft:
        lines.append(f"\n**Cover letter:**\n\n{draft}")
    elif error:
        lines.append(f"\n_{error}_")
    return "\n".join(lines)


warn = log.warn

# Search settings recorded with each run (shown in the dashboard's run history)
RUN_PARAM_KEYS = ("keywords", "location", "sources", "part_time", "term_break", "fits_only",
                  "show_blocked", "show_seen", "limit", "max_score", "draft_top", "resume",
                  "work_mode", "pay", "job_type")


def _choices(allowed):
    def parse(value):
        try:
            return classify.parse_choice_list(value, allowed)
        except ValueError as e:
            raise argparse.ArgumentTypeError(str(e))
    return parse


class RunFailed(Exception):
    """A search couldn't run at all (missing resume, every source down...)."""


def describe(error):
    """LLMError messages are already readable; for anything else include the type."""
    return str(error) if isinstance(error, llm.LLMError) else f"{type(error).__name__}: {error}"


def fetch_all(source_names, keywords, location, part_time=False):
    jobs, failures = [], []
    for name in source_names:
        fetch = sources_mod.SOURCES.get(name)
        if not fetch:
            failures.append(f"Unknown source: {name}")
            continue
        try:
            jobs.extend(fetch(keywords=keywords, location=location, part_time=part_time))
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


def handle_discover(args):
    print(f"Looking for {args.discover} on Greenhouse, Lever, Ashby, Workable and SmartRecruiters...")
    hits = ats.discover(args.discover)
    if not hits:
        print("No public careers feed found. They may use Workday or their own site; try a different spelling.")
        return
    best = max(hits, key=lambda h: h["jobs"])
    for h in hits:
        print(f"  {h['ats']:16} slug={h['slug']:20} {h['jobs']} open job(s)")
    if ats.add_company(args.companies, best):
        print(f"Added {best['name']} ({best['ats']}/{best['slug']}) to {args.companies}")
    else:
        print(f"{best['name']} is already in {args.companies}")


def apply_student_mode(args, jobs):
    """Annotate jobs with visa/hours checks and drop ones that don't suit a student."""
    available = student_mod.available_fortnight_hours(args.term_break)
    if available is None:
        warn("Course-break mode: no hours cap applied.")
    else:
        cap = float(os.getenv("VISA_FORTNIGHT_CAP", "48"))
        warn(f"Visa hours: {available:g} of {cap:g} h/fortnight free "
             f"(set COMMITTED_FORTNIGHT_HOURS in .env to the hours your other jobs use).")

    kept, dropped_type, dropped_blocked, dropped_hours = [], 0, 0, 0
    for job in jobs:
        job.student = student_mod.check_job(job, available)
        if not job.student.is_part_time_friendly:
            dropped_type += 1
        elif job.student.verdict == "blocked" and not args.show_blocked:
            dropped_blocked += 1
        elif args.fits_only and job.student.verdict not in ("fits", "no_cap"):
            dropped_hours += 1
        else:
            kept.append(job)
    if dropped_type:
        warn(f"Dropped {dropped_type} full-time or unclear job(s).")
    if dropped_blocked:
        warn(f"Dropped {dropped_blocked} job(s) needing citizenship/PR/clearance/full work rights (--show-blocked to see).")
    if dropped_hours:
        warn(f"Dropped {dropped_hours} job(s) that don't fit your free hours.")
    return sorted(kept, key=student_mod.sort_key)


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
    os.environ["COMPANIES_FILE"] = args.companies
    if args.discover:
        handle_discover(args)
        return
    store = JobStore(args.db)
    try:
        if handle_store_commands(args, store):
            return
        run_search(args, store)
    except RunFailed:
        sys.exit(1)  # already reported
    finally:
        store.close()


def run_search(args, store, trigger="cli", write_output=True):
    """Run one search and record it (with its log and counts) in the runs table.
    Returns (run_id, stats). Raises RunFailed if the search couldn't run."""
    params = {k: getattr(args, k, None) for k in RUN_PARAM_KEYS}
    run_id = store.start_run(trigger, params)
    stats = {"fetched": 0, "matched": 0, "new": 0, "scored": 0, "drafted": 0, "shown": 0}
    with log.capture() as lines:
        try:
            _search(args, store, run_id, stats, write_output)
        except RunFailed as e:
            warn(str(e))
            store.finish_run(run_id, "failed", stats, lines, str(e))
            raise
        except Exception as e:
            warn(f"Run crashed: {describe(e)}")
            store.finish_run(run_id, "failed", stats, lines, describe(e))
            raise
        store.finish_run(run_id, "done", stats, lines)
    return run_id, stats


def _search(args, store, run_id, stats, write_output):
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None
    source_names = (
        [s.strip().lower() for s in args.sources.split(",") if s.strip()]
        if args.sources else sources_mod.default_sources()
    )

    resume_text = None
    if args.resume:
        if not os.path.exists(args.resume):
            raise RunFailed(f"Resume file not found: {args.resume}")
        with open(args.resume) as f:
            resume_text = f.read()

    if resume_text:
        problem = llm.config_problem()
        if problem:
            warn(f"{problem} Showing jobs without AI scoring or cover notes.")
            resume_text = None

    try:
        jobs = fetch_all(source_names, keywords, args.location, part_time=args.part_time)
    except SourceError as e:
        raise RunFailed(str(e)) from e
    stats["fetched"] = len(jobs)

    matched = filter_jobs(jobs, keywords=keywords, location=args.location)
    stats["matched"] = len(matched)
    if not args.show_seen:
        known = store.known_ids()
        hidden = sum(1 for j in matched if j.id in known)
        matched = [j for j in matched if j.id not in known]
        if hidden:
            warn(f"Hid {hidden} job(s) you've already seen (use --show-seen to include them).")

    for job in matched:
        classify.classify(job)
    matched = classify.apply_filters(matched, args.work_mode, args.pay, args.job_type)

    if args.part_time:
        matched = apply_student_mode(args, matched)
    stats["new"] = len(matched)

    if not matched:
        log.info("No new matching jobs found.")
        return

    if resume_text:
        to_score = matched[: args.max_score]
        failures = 0
        for job in to_score:
            try:
                score_job(job, resume_text)
                failures = 0
            except Exception as e:  # unscored jobs sink to the bottom
                failures += 1
                warn(f"Scoring failed for '{job.title}': {describe(e)}")
                if failures >= MAX_LLM_FAILURES_IN_A_ROW:
                    warn(f"Stopping AI scoring after {failures} failures in a row "
                         "(rate limit, used-up free quota, or a bad key?). Remaining jobs are unscored.")
                    break
        stats["scored"] = sum(1 for j in to_score if j.score is not None)
        matched = sort_by_score(to_score)
        if args.part_time:
            matched = sorted(matched, key=student_mod.sort_key)

    matched = matched[: args.limit]

    if not write_output:
        out = io.StringIO()
    else:
        out = open(args.output, "w") if args.output else sys.stdout
    drafted, draft_failures = 0, 0
    try:
        for i, job in enumerate(matched):
            draft, error = None, None
            if (resume_text and job.score is not None and drafted < args.draft_top
                    and draft_failures < MAX_LLM_FAILURES_IN_A_ROW):
                drafted += 1
                try:
                    draft = draft_application(job, resume_text)
                    draft_failures = 0
                    stats["drafted"] += 1
                except Exception as e:  # keep going; one bad call shouldn't lose the rest
                    draft_failures += 1
                    error = f"Draft failed: {describe(e)}"
                    warn(f"Draft failed for '{job.title}': {describe(e)}")
            if i:
                out.write(SEPARATOR)
            out.write(format_job(job, draft, error))
            out.flush()  # each result is saved as soon as it's produced
            store.save_job(job, run_id=run_id, draft=draft, draft_error=error)
            stats["shown"] += 1
        out.write("\n")
    finally:
        if out is not sys.stdout:
            out.close()

    if write_output and args.output:
        log.info(f"Wrote {len(matched)} job(s) to {args.output}")


if __name__ == "__main__":
    main()
