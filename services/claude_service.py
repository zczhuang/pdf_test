"""
Claude API service — auto-tags journal entries and generates weekly summaries
using claude-opus-4-6 with adaptive thinking and streaming.
"""

import json
import anthropic

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

Return ONLY a JSON array of strings. No other text.
"""


def tag_entry(text: str) -> list[str]:
    """
    Use Claude to auto-tag a journal entry.
    Returns a list of tag strings like ["work", "gratitude", "career growth"].
    """
    client = anthropic.Anthropic()

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        system=_SYSTEM_PROMPT_TAG,
        messages=[{"role": "user", "content": text}],
    )

    raw = response.content[0].text.strip()
    try:
        tags = json.loads(raw)
        if isinstance(tags, list):
            return [str(t) for t in tags[:6]]
    except (json.JSONDecodeError, IndexError):
        pass
    return []


def generate_period_summary(entries: list[dict], period_label: str, period_kind: str) -> str:
    """
    Generate a concise gallery-ready summary for a selected period.

    Args:
        entries: List of dicts with keys: date (str), content (str), tags (list[str]).
        period_label: Human-readable label like "Week of March 3, 2026".
        period_kind: One of "week", "month", or "ytd".
    """
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

    client = anthropic.Anthropic()

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=2500,
        thinking={"type": "adaptive"},
        system=_SYSTEM_PROMPT_GALLERY_SUMMARY,
        messages=[
            {
                "role": "user",
                "content": (
                    f"These are my journal entries for this {period_kind}: {period_label}.\n\n"
                    f"There are {len(entries)} entries in this period.\n\n"
                    f"{entries_text}"
                ),
            }
        ],
    ) as stream:
        final = stream.get_final_message()

    return next(
        (block.text for block in final.content if block.type == "text"),
        "Summary unavailable.",
    )


def generate_weekly_summary(entries: list[dict], week_label: str) -> str:
    """
    Generate a weekly summary from a list of journal entries.

    Args:
        entries:    List of dicts with keys: date (str), content (str), tags (list[str]).
        week_label: Human-readable label like "Week of March 3, 2026".

    Returns:
        Markdown-formatted summary string.
    """
    if not entries:
        return f"# Weekly Summary — {week_label}\n\nNo entries recorded this week."

    entries_text = "\n\n".join(
        f"### {e['date']}{' — ' + ', '.join(e.get('tags', [])) if e.get('tags') else ''}\n\n{e['content']}"
        for e in entries
    )

    client = anthropic.Anthropic()

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=_SYSTEM_PROMPT_SUMMARY,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Here are my journal entries for {week_label}. "
                    "Please write my weekly summary.\n\n"
                    f"{entries_text}"
                ),
            }
        ],
    ) as stream:
        final = stream.get_final_message()

    text = next(
        (block.text for block in final.content if block.type == "text"),
        "Summary unavailable.",
    )

    header = f"# Weekly Summary — {week_label}\n\n"
    return header + text
