import argparse
import os
import sys

from dotenv import load_dotenv

from job_agent.drafter import draft_application
from job_agent.matcher import filter_jobs
from job_agent.sources import fetch_remoteok_jobs


def parse_args():
    parser = argparse.ArgumentParser(description="Search jobs and draft tailored cover notes.")
    parser.add_argument("--keywords", help="Comma-separated keywords to match (e.g. 'python,backend')")
    parser.add_argument("--location", help="Location filter (e.g. 'remote')")
    parser.add_argument("--resume", help="Path to a plain-text resume file. Enables AI-drafted cover notes.")
    parser.add_argument("--limit", type=int, default=10, help="Max number of matching jobs to show")
    parser.add_argument("--output", help="Path to write results as Markdown (defaults to stdout)")
    return parser.parse_args()


def format_job(job, draft=None):
    lines = [
        f"## {job.title} — {job.company}",
        f"- Location: {job.location}",
        f"- URL: {job.url}",
    ]
    if draft:
        lines.append(f"\n**Draft cover note:**\n\n{draft}")
    return "\n".join(lines)


def main():
    load_dotenv()
    args = parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None

    jobs = fetch_remoteok_jobs()
    matched = filter_jobs(jobs, keywords=keywords, location=args.location)[: args.limit]

    if not matched:
        print("No matching jobs found.")
        return

    resume_text = None
    if args.resume:
        if not os.path.exists(args.resume):
            print(f"Resume file not found: {args.resume}", file=sys.stderr)
            sys.exit(1)
        with open(args.resume) as f:
            resume_text = f.read()

    sections = []
    for job in matched:
        draft = draft_application(job, resume_text) if resume_text else None
        sections.append(format_job(job, draft))

    output = "\n\n---\n\n".join(sections)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Wrote {len(matched)} job(s) to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
