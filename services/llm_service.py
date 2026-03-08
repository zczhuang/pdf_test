"""
Gemini API service — auto-tags journal entries and generates summaries.
"""

from __future__ import annotations

import json
import os

from google import genai
from google.genai import types

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview-06-17"

_SYSTEM_PROMPT_SUMMARY = """\
You are a warm, thoughtful journaling companion. The user keeps a daily reflective \
journal with free-form thoughts spanning work, life, faith, and everything in between.

When given a week's entries, write a weekly summary that:
1. Opens with a one-sentence "theme of the week" — the single thread running through everything.
2. Groups insights by the natural themes that emerged (NOT predefined categories).
3. Identifies meaningful connections, patterns, or growth across entries.
4. Closes with a short encouragement or question to carry into the next week.

Tone: warm, reflective, honest — like a wise friend who has been paying attention.
Format: Markdown with clear headings.
Length: 300–500 words.
"""

_SYSTEM_PROMPT_GALLERY_SUMMARY = """\
You are a warm, thoughtful journaling companion. The user keeps a reflective \
journal that includes daily notes, voice transcripts, and attached media.

When given entries for a time period, write a compact review in Markdown with:
1. A `## Recent highlights` section with 3-5 concrete bullet points.
2. A `## Summary` section with 1-3 short paragraphs that synthesize the period.
3. A `## What seems to be building` section with 1-3 bullets about patterns, momentum, \
   or unresolved tension.

Requirements:
- Refer to the actual themes and moments in the entries.
- Keep the tone warm, perceptive, and grounded.
- Avoid generic self-help language.
- Keep it concise enough to scan comfortably inside a gallery view.
- Do not mention missing future dates or speculate beyond the entries provided.
"""

_SYSTEM_PROMPT_TAG = """\
You are a journal entry tagger. Given a journal entry, produce a JSON array of 2–6 short tags \
that capture the key themes, topics, emotions, or domains. Tags should be lowercase, 1–3 words each.

Examples of good tags: "work", "faith", "gratitude", "career growth", "family", "health", \
"prayer", "frustration", "big win", "learning", "relationships", "rest".

The entry may be in English, Mandarin Chinese, or a mix. Always output tags in English.
"""


def get_summary_model() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def tag_entry(text: str) -> list[str]:
    """Use Gemini to auto-tag a journal entry."""
    client = _get_client()
    response = client.models.generate_content(
        model=get_summary_model(),
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT_TAG,
            response_mime_type="application/json",
            response_json_schema={
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 6,
            },
            temperature=0.1,
            max_output_tokens=128,
        ),
    )

    raw = _response_text(response).strip()
    try:
        tags = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(tags, list):
        return []

    normalized: list[str] = []
    for item in tags:
        tag = str(item).strip().lower()
        if tag and tag not in normalized:
            normalized.append(tag)
        if len(normalized) >= 6:
            break
    return normalized


def generate_period_summary(entries: list[dict], period_label: str, period_kind: str) -> str:
    """Generate a concise gallery-ready summary for a selected period."""
    if not entries:
        return (
            "## Recent highlights\n"
            "- No journal entries were recorded for this period.\n\n"
            "## Summary\n"
            f"There is nothing to summarize for {period_label.lower()} yet.\n\n"
            "## What seems to be building\n"
            "- Start a note in this period to begin building a pattern."
        )

    entries_text = "\n\n".join(
        f"### {entry['date']}{' - ' + ', '.join(entry.get('tags', [])) if entry.get('tags') else ''}\n\n{entry['content']}"
        for entry in entries
    )

    client = _get_client()
    response = client.models.generate_content(
        model=get_summary_model(),
        contents=(
            f"These are my journal entries for this {period_kind}: {period_label}.\n\n"
            f"There are {len(entries)} entries in this period.\n\n"
            f"{entries_text}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT_GALLERY_SUMMARY,
            temperature=0.5,
            max_output_tokens=1800,
        ),
    )
    return _response_text(response).strip() or "Summary unavailable."


def generate_weekly_summary(entries: list[dict], week_label: str) -> str:
    """Generate a weekly recap from a list of journal entries."""
    if not entries:
        return f"# Weekly Summary — {week_label}\n\nNo entries recorded this week."

    entries_text = "\n\n".join(
        f"### {entry['date']}{' — ' + ', '.join(entry.get('tags', [])) if entry.get('tags') else ''}\n\n{entry['content']}"
        for entry in entries
    )

    client = _get_client()
    response = client.models.generate_content(
        model=get_summary_model(),
        contents=(
            f"Here are my journal entries for {week_label}. "
            "Please write my weekly summary.\n\n"
            f"{entries_text}"
        ),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT_SUMMARY,
            temperature=0.55,
            max_output_tokens=2200,
        ),
    )

    text = _response_text(response).strip() or "Summary unavailable."
    return f"# Weekly Summary — {week_label}\n\n{text}"


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return genai.Client(api_key=api_key)


def _response_text(response: object) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    return ""
