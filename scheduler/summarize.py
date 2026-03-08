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
    Load or generate the weekly summary and save it when needed.

    Args:
        week_start_override: Optional YYYY-MM-DD to summarize a specific week.
                             Defaults to the current ISO week start (Monday).
    Returns:
        Result dict with success/error and drive_url.
    """
    from app import SUMMARY_KIND_WEEKLY_RECAP, _period_window, _resolve_summary
    from services.llm_service import generate_weekly_summary

    now = datetime.now(timezone.utc)

    if week_start_override:
        week_start = datetime.strptime(week_start_override, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    else:
        # Monday of current ISO week
        week_start = now - timedelta(days=now.weekday())

    window = _period_window("week", week_start.date())
    week_label = window["label"]
    week_iso = window["period_key"]

    print(f"Resolving summary for {week_label}…")
    result = _resolve_summary(
        summary_kind=SUMMARY_KIND_WEEKLY_RECAP,
        period="week",
        period_key=week_iso,
        label=week_label,
        start_date=window["start_date"],
        end_date=window["end_date"],
        generator=lambda entries, label: generate_weekly_summary(entries, label),
    )

    print(f"Summary {result['summary_source']} at: {result['drive_url']}")
    return {
        "success": True,
        "week": week_iso,
        "entries": result["entries_count"],
        "drive_url": result["drive_url"],
        "summary_source": result["summary_source"],
    }


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
