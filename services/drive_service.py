"""
Google Drive service — reads/writes journal entries and media as files in Drive.

Folder structure created automatically:
  <DRIVE_FOLDER_ID>/
    entries/        <- one Markdown file per day (YYYY-MM-DD.md)
    media/          <- audio, image uploads
    summaries/
      weekly/       <- one Markdown file per week (YYYY-Wnn.md)

Auth: Uses OAuth2 refresh token (personal Google account) so files are owned by
you and count against your 15 GB quota. Set env vars:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
"""

import io
import os
import re
from datetime import datetime
from typing import BinaryIO
from urllib.parse import parse_qs, urlparse

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload, MediaIoBaseUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

SUBFOLDER_ENTRIES = "entries"
SUBFOLDER_MEDIA = "media"
SUBFOLDER_SUMMARIES = "summaries"
SUBFOLDER_WEEKLY = "weekly"

_folder_id_cache: dict[str, str] = {}

_ENTRY_HEADER_RE = re.compile(r"^# Journal [—-] (?P<date>\d{4}-\d{2}-\d{2})$", re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"^- \[(?P<label>.+?)\]\((?P<url>https?://[^\s)]+)\)$")
_LABEL_URL_RE = re.compile(r"^- (?P<label>.+): (?P<url>https?://\S+)$")

_IMAGE_EXTENSIONS = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp", ".heic"}
_VIDEO_EXTENSIONS = {".m4v", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
_AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}


def _get_service():
    """Build Drive service using personal OAuth2 credentials."""
    # Try OAuth2 refresh token first (personal Drive)
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")

    if client_id and client_secret and refresh_token:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        creds.refresh(Request())
    else:
        # Fallback to service account
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_file(
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            scopes=SCOPES,
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _find_or_create_folder(service, name: str, parent_id: str) -> str:
    cache_key = f"{parent_id}/{name}"
    if cache_key in _folder_id_cache:
        return _folder_id_cache[cache_key]

    query = (
        f"name = '{name}' and "
        f"mimeType = 'application/vnd.google-apps.folder' and "
        f"'{parent_id}' in parents and trashed = false"
    )
    results = service.files().list(q=query, fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = results.get("files", [])

    if files:
        folder_id = files[0]["id"]
    else:
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = service.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
        folder_id = folder["id"]

    _folder_id_cache[cache_key] = folder_id
    return folder_id


def _get_entries_folder(service) -> str:
    root = os.environ["DRIVE_FOLDER_ID"]
    return _find_or_create_folder(service, SUBFOLDER_ENTRIES, root)


def _get_media_folder(service) -> str:
    root = os.environ["DRIVE_FOLDER_ID"]
    return _find_or_create_folder(service, SUBFOLDER_MEDIA, root)


def _get_weekly_summaries_folder(service) -> str:
    root = os.environ["DRIVE_FOLDER_ID"]
    summaries_id = _find_or_create_folder(service, SUBFOLDER_SUMMARIES, root)
    return _find_or_create_folder(service, SUBFOLDER_WEEKLY, summaries_id)


def write_entry(
    date: str,
    text: str,
    tags: list[str] | None = None,
    media_urls: list[dict] | None = None,
    media_links: list[dict] | None = None,
) -> str:
    """Write a daily journal entry Markdown file to Drive. Returns the file's Drive URL."""
    service = _get_service()
    entries_folder = _get_entries_folder(service)

    timestamp = datetime.utcnow().strftime("%H:%M UTC")
    lines = [
        f"# Journal — {date}",
        "",
        f"**Time:** {timestamp}",
    ]

    if tags:
        lines.append(f"**Tags:** {', '.join(tags)}")

    lines += [
        "",
        text.strip(),
        "",
    ]

    # Media URLs (YouTube, Google Photos, etc.)
    if media_urls or media_links:
        lines += ["## Media", ""]
        if media_urls:
            for link in media_urls:
                lines.append(f"- [{link['label']}]({link['url']})")
        if media_links:
            for link in media_links:
                lines.append(f"- {link['label']}: {link['url']}")
        lines.append("")

    lines += [
        "---",
        f"*Created: {date} {timestamp}*",
    ]

    content = "\n".join(lines)
    filename = f"{date}.md"

    # Check if an entry already exists for today — append if so
    existing = _find_file(service, filename, entries_folder)
    if existing:
        existing_content = _download_file(service, existing["id"])
        content = existing_content + "\n\n---\n\n" + content
        _update_file(service, existing["id"], content.encode())
        file_id = existing["id"]
    else:
        file_id = _create_file(
            service,
            filename,
            content.encode(),
            "text/markdown",
            entries_folder,
        )

    return f"https://drive.google.com/file/d/{file_id}/view"


def upload_media(filename: str, data: bytes, mime_type: str) -> dict:
    """Upload a media file (audio, image) to the Drive media folder. Returns link info."""
    service = _get_service()
    media_folder = _get_media_folder(service)
    file_id = _create_file(service, filename, data, mime_type, media_folder)
    return {
        "label": filename,
        "url": f"https://drive.google.com/file/d/{file_id}/view",
        "file_id": file_id,
    }


def upload_media_stream(filename: str, file_obj: BinaryIO, mime_type: str) -> dict:
    """
    Upload a media file from a file-like object using Drive resumable upload.

    This avoids buffering larger videos fully in application memory before the
    Drive upload starts.
    """
    service = _get_service()
    media_folder = _get_media_folder(service)

    try:
        file_obj.seek(0)
    except (AttributeError, OSError):
        pass

    file_id = _create_file_from_stream(
        service,
        filename,
        file_obj,
        mime_type,
        media_folder,
    )
    return {
        "label": filename,
        "url": f"https://drive.google.com/file/d/{file_id}/view",
        "file_id": file_id,
    }


def write_summary(week_label: str, content: str) -> str:
    """Write a weekly summary Markdown file to Drive. Returns Drive URL."""
    service = _get_service()
    folder = _get_weekly_summaries_folder(service)
    filename = f"{week_label}.md"

    existing = _find_file(service, filename, folder)
    if existing:
        _update_file(service, existing["id"], content.encode())
        file_id = existing["id"]
    else:
        file_id = _create_file(service, filename, content.encode(), "text/markdown", folder)

    return f"https://drive.google.com/file/d/{file_id}/view"


def list_entry_dates(limit: int | None = None) -> list[dict]:
    """Return available journal dates in descending order."""
    service = _get_service()
    files = _list_entry_files(service, order_by="name desc")
    dates = []

    for file_info in files:
        date_str = file_info["name"].replace(".md", "")
        if not _is_iso_date(date_str):
            continue
        dates.append(
            {
                "date": date_str,
                "drive_url": _drive_file_url(file_info["id"]),
            }
        )
        if limit and len(dates) >= limit:
            break

    return dates


def get_entry_day(date: str) -> dict | None:
    """Return a parsed journal day for gallery rendering."""
    service = _get_service()
    entries_folder = _get_entries_folder(service)
    filename = f"{date}.md"
    existing = _find_file(service, filename, entries_folder)

    if not existing:
        return None

    raw = _download_file(service, existing["id"])
    entries = _parse_entry_document(raw)
    return {
        "date": date,
        "drive_url": _drive_file_url(existing["id"]),
        "entry_count": len(entries),
        "entries": entries,
    }


def list_entries(since_date: str | None = None) -> list[dict]:
    """
    Return a list of journal entries as dicts with keys: date, content, tags.
    Optionally filter to entries on or after `since_date` (YYYY-MM-DD).
    """
    service = _get_service()
    files = _list_entry_files(service, order_by="name")

    entries = []
    for file_info in files:
        date_str = file_info["name"].replace(".md", "")
        if since_date and date_str < since_date:
            continue
        raw = _download_file(service, file_info["id"])
        entries.append({"date": date_str, "content": raw, "tags": _extract_tags(raw)})

    return entries


# ── low-level Drive helpers ──────────────────────────────────────────────────

def _list_entry_files(service, order_by: str) -> list[dict]:
    entries_folder = _get_entries_folder(service)
    query = f"'{entries_folder}' in parents and trashed = false and name contains '.md'"
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            orderBy=order_by,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return results.get("files", [])


def _find_file(service, name: str, parent_id: str) -> dict | None:
    query = f"name = '{name}' and '{parent_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    files = results.get("files", [])
    return files[0] if files else None


def _create_file(
    service, name: str, data: bytes, mime_type: str, parent_id: str
) -> str:
    metadata = {"name": name, "parents": [parent_id]}
    media = MediaInMemoryUpload(data, mimetype=mime_type)
    f = service.files().create(body=metadata, media_body=media, fields="id", supportsAllDrives=True).execute()
    return f["id"]


def _create_file_from_stream(
    service,
    name: str,
    file_obj: BinaryIO,
    mime_type: str,
    parent_id: str,
) -> str:
    metadata = {"name": name, "parents": [parent_id]}
    media = MediaIoBaseUpload(file_obj, mimetype=mime_type, resumable=True)
    request = service.files().create(
        body=metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    )

    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]


def _update_file(service, file_id: str, data: bytes) -> None:
    media = MediaInMemoryUpload(data, mimetype="text/markdown")
    service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()


def _download_file(service, file_id: str) -> str:
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue().decode("utf-8")


def _extract_tags(content: str) -> list[str]:
    """Pull tags from a Markdown entry header."""
    for line in content.splitlines():
        if line.startswith("**Tags:**"):
            raw = line.replace("**Tags:**", "").strip()
            return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def _parse_entry_document(content: str) -> list[dict]:
    matches = list(_ENTRY_HEADER_RE.finditer(content))
    if not matches:
        return []

    entries = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        block = content[start:end].strip()
        parsed = _parse_entry_block(block)
        if parsed:
            entries.append(parsed)

    return entries


def _parse_entry_block(block: str) -> dict | None:
    lines = [line.rstrip() for line in block.splitlines()]
    if not lines:
        return None

    match = _ENTRY_HEADER_RE.match(lines[0])
    if not match:
        return None

    entry_date = match.group("date")
    entry_time = ""
    created_at = ""
    tags: list[str] = []
    body_lines: list[str] = []
    media_lines: list[str] = []
    in_media_section = False

    for line in lines[1:]:
        stripped = line.strip()

        if line.startswith("**Time:**"):
            entry_time = line.replace("**Time:**", "", 1).strip()
            continue
        if line.startswith("**Tags:**"):
            raw_tags = line.replace("**Tags:**", "", 1).strip()
            tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
            continue
        if stripped == "## Media":
            in_media_section = True
            continue
        if line.startswith("*Created:") and line.endswith("*"):
            created_at = line[1:-1].replace("Created:", "", 1).strip()
            break
        if stripped == "---":
            continue

        if in_media_section:
            media_lines.append(line)
        else:
            body_lines.append(line)

    return {
        "date": entry_date,
        "time": entry_time,
        "created_at": created_at,
        "tags": tags,
        "text": "\n".join(body_lines).strip(),
        "media": _parse_media_lines(media_lines),
    }


def _parse_media_lines(lines: list[str]) -> list[dict]:
    media_items = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        match = _MARKDOWN_LINK_RE.match(stripped) or _LABEL_URL_RE.match(stripped)
        if not match:
            continue

        label = match.group("label").strip()
        url = match.group("url").strip()
        media_items.append(_describe_media_item(label, url))

    return media_items


def _describe_media_item(label: str, url: str) -> dict:
    kind = _infer_media_kind(label, url)
    preview_url = _build_preview_url(url, kind)
    return {
        "label": label,
        "url": url,
        "kind": kind,
        "preview_url": preview_url,
    }


def _infer_media_kind(label: str, url: str) -> str:
    lowered_label = (label or "").lower()

    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if lowered_label == "voice recording" or lowered_label.startswith("voice recording"):
        return "audio"

    candidates = [lowered_label]
    path = urlparse(url).path.lower()
    if path:
        candidates.append(path)

    for candidate in candidates:
        for extension in _IMAGE_EXTENSIONS:
            if candidate.endswith(extension):
                return "image"
        for extension in _VIDEO_EXTENSIONS:
            if candidate.endswith(extension):
                return "video"
        for extension in _AUDIO_EXTENSIONS:
            if candidate.endswith(extension):
                return "audio"

    return "link"


def _build_preview_url(url: str, kind: str) -> str | None:
    if kind == "youtube":
        video_id = _extract_youtube_id(url)
        if video_id:
            return f"https://www.youtube.com/embed/{video_id}"
        return None

    drive_file_id = _extract_drive_file_id(url)
    if drive_file_id:
        if kind == "image":
            return f"https://drive.google.com/thumbnail?id={drive_file_id}&sz=w1200"
        return f"https://drive.google.com/file/d/{drive_file_id}/preview"

    if kind in {"image", "video", "audio"}:
        return url

    return None


def _extract_drive_file_id(url: str) -> str | None:
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if match:
        return match.group(1)

    parsed = urlparse(url)
    query_id = parse_qs(parsed.query).get("id")
    if query_id:
        return query_id[0]

    return None


def _extract_youtube_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/") or None
    if "youtube.com" in parsed.netloc:
        video_id = parse_qs(parsed.query).get("v")
        if video_id:
            return video_id[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
    return None
