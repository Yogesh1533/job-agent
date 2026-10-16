import argparse
import os
import sys

from dotenv import load_dotenv

from job_agent.drafter import draft_application
from job_agent.matcher import filter_jobs
from job_agent.sources import SourceError, fetch_remoteok_jobs

SEPARATOR = "\n\n---\n\n"


def parse_args():
    parser = argparse.ArgumentParser(description="Search jobs and draft tailored cover notes.")
    parser.add_argument("--keywords", help="Comma-separated keywords to match (e.g. 'python,backend')")
    parser.add_argument("--location", help="Location filter (e.g. 'remote')")
    parser.add_argument("--resume", help="Path to a plain-text resume file. Enables AI-drafted cover notes.")
    parser.add_argument("--limit", type=int, default=10, help="Max number of matching jobs to show")
    parser.add_argument("--output", help="Path to write results as Markdown (defaults to stdout)")
    return parser.parse_args()


def format_job(job, draft=None, error=None):
    lines = [
        f"## {job.title} — {job.company}",
        f"- Location: {job.location}",
        f"- URL: {job.url}",
    ]
    if draft:
        lines.append(f"\n**Draft cover note:**\n\n{draft}")
    elif error:
        lines.append(f"\n_Draft failed: {error}_")
    return "\n".join(lines)


def main():
    load_dotenv()
    args = parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()] if args.keywords else None

    resume_text = None
    if args.resume:
        if not os.path.exists(args.resume):
            print(f"Resume file not found: {args.resume}", file=sys.stderr)
            sys.exit(1)
        with open(args.resume) as f:
            resume_text = f.read()

    try:
        jobs = fetch_remoteok_jobs()
    except SourceError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    matched = filter_jobs(jobs, keywords=keywords, location=args.location)[: args.limit]

    if not matched:
        print("No matching jobs found.")
        return

    out = open(args.output, "w") if args.output else sys.stdout
    failures = 0
    try:
        for i, job in enumerate(matched):
            draft, error = None, None
            if resume_text:
                try:
                    draft = draft_application(job, resume_text)
                except Exception as e:  # keep going; one bad call shouldn't lose the rest
                    error = f"{type(e).__name__}: {e}"
                    failures += 1
                    print(f"Draft failed for '{job.title}': {error}", file=sys.stderr)
            if i:
                out.write(SEPARATOR)
            out.write(format_job(job, draft, error))
            out.flush()  # each result is saved as soon as it's produced
        out.write("\n")
    finally:
        if out is not sys.stdout:
            out.close()

    if args.output:
        print(f"Wrote {len(matched)} job(s) to {args.output}" + (f" ({failures} draft(s) failed)" if failures else ""))


if __name__ == "__main__":
    main()
