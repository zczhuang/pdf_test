"""
YouTube link enrichment via YouTube Data API v3.

Given a YouTube URL, fetches title, channel name, and description
so they can be embedded in the journal entry for context.
"""

import os
import re

import requests

_YT_API_BASE = "https://www.googleapis.com/youtube/v3/videos"

_VIDEO_ID_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})",
    r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
]


def enrich_youtube_url(url: str) -> dict:
    """
    Fetch metadata for a YouTube video.

    Returns a dict with keys: title, channel, description, video_id, url.
    Returns minimal info (just the url) if the API key is absent or the
    request fails — so this is always safe to call.
    """
    video_id = _extract_video_id(url)
    if not video_id:
        return {"title": url, "url": url}

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return {"title": url, "url": url, "video_id": video_id}

    try:
        resp = requests.get(
            _YT_API_BASE,
            params={
                "id": video_id,
                "key": api_key,
                "part": "snippet",
                "fields": "items(snippet(title,channelTitle,description))",
            },
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return {"title": url, "url": url, "video_id": video_id}

        snippet = items[0]["snippet"]
        return {
            "video_id": video_id,
            "url": url,
            "title": snippet.get("title", url),
            "channel": snippet.get("channelTitle", ""),
            "description": snippet.get("description", "")[:500],
        }
    except Exception:
        return {"title": url, "url": url, "video_id": video_id}


def _extract_video_id(url: str) -> str | None:
    for pattern in _VIDEO_ID_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None
