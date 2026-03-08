"""
Claude API service — generates weekly summaries using claude-opus-4-6 with
adaptive thinking and streaming for reliable, thoughtful output.
"""

import anthropic

_SYSTEM_PROMPT = """\
You are a warm, thoughtful journaling companion. The user keeps a daily reflective \
journal across three life domains: Work, Life, and Faith.

When given a week's entries, write a weekly summary that:
1. Opens with a one-sentence "theme of the week" — the single thread running through everything.
2. Reflects on each domain (Work, Life, Faith) in 2–4 sentences, noting growth, \
challenges, or notable moments.
3. Identifies any meaningful connections between the domains.
4. Closes with a short encouragement or question to carry into the next week.

Tone: warm, reflective, honest — like a wise friend who has been paying attention.
Format: Markdown with clear headings.
Length: 300–500 words.
"""


def generate_weekly_summary(entries: list[dict], week_label: str) -> str:
    """
    Generate a weekly summary from a list of journal entries.

    Args:
        entries:    List of dicts with keys: date (str), category (str), content (str).
        week_label: Human-readable label like "Week of March 3, 2026".

    Returns:
        Markdown-formatted summary string.
    """
    if not entries:
        return f"# Weekly Summary — {week_label}\n\nNo entries recorded this week."

    entries_text = "\n\n".join(
        f"### {e['date']} ({e.get('category', 'general').capitalize()})\n\n{e['content']}"
        for e in entries
    )

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    with client.messages.stream(
        model="claude-opus-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=_SYSTEM_PROMPT,
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

    # Extract the text block (adaptive thinking may also produce a thinking block)
    text = next(
        (block.text for block in final.content if block.type == "text"),
        "Summary unavailable.",
    )

    header = f"# Weekly Summary — {week_label}\n\n"
    return header + text
