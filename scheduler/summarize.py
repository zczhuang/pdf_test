"""
Weekly summarizer — called by Cloud Scheduler every Sunday.

Can also be run directly:
  python -m scheduler.summarize

Cloud Scheduler setup (GCP Console → Cloud Scheduler):
  Schedule:    0 21 * * 0         (9 PM UTC every Sunday)
  Target:      HTTP
  URL:         https://<your-cloud-run-url>/summarize
  Method:      POST
  Body:        {}
  Headers:     Content-Type: application/json
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as `python -m scheduler.summarize` from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


def run_weekly_summary(week_start_override: str | None = None) -> dict:
    """
    Generate and save the weekly summary.

    Args:
        week_start_override: Optional YYYY-MM-DD to summarize a specific week.
                             Defaults to the current ISO week start (Monday).
    Returns:
        Result dict with success/error and drive_url.
    """
    from services.claude_service import generate_weekly_summary
    from services.drive_service import list_entries, write_summary

    now = datetime.now(timezone.utc)

    if week_start_override:
        week_start = datetime.strptime(week_start_override, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    else:
        # Monday of current ISO week
        week_start = now - timedelta(days=now.weekday())

    since_date = week_start.strftime("%Y-%m-%d")
    week_label = f"Week of {week_start.strftime('%B %-d, %Y')}"
    week_iso = week_start.strftime("%G-W%V")

    print(f"Summarizing {week_label} (entries since {since_date})…")

    entries = list_entries(since_date=since_date)
    print(f"Found {len(entries)} entries.")

    summary_md = generate_weekly_summary(entries, week_label)
    drive_url = write_summary(week_iso, summary_md)

    print(f"Summary written to Drive: {drive_url}")
    return {"success": True, "week": week_iso, "entries": len(entries), "drive_url": drive_url}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a weekly journal summary")
    parser.add_argument(
        "--week-start",
        help="Override week start date (YYYY-MM-DD). Defaults to current week.",
        default=None,
    )
    args = parser.parse_args()

    result = run_weekly_summary(args.week_start)
    print(json.dumps(result, indent=2))
