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
import json
import os
from datetime import datetime

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

SUBFOLDER_ENTRIES = "entries"
SUBFOLDER_MEDIA = "media"
SUBFOLDER_SUMMARIES = "summaries"
SUBFOLDER_WEEKLY = "weekly"

_folder_id_cache: dict[str, str] = {}


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


def list_entries(since_date: str | None = None) -> list[dict]:
    """
    Return a list of journal entries as dicts with keys: date, content, tags.
    Optionally filter to entries on or after `since_date` (YYYY-MM-DD).
    """
    service = _get_service()
    entries_folder = _get_entries_folder(service)

    query = f"'{entries_folder}' in parents and trashed = false and name contains '.md'"
    results = (
        service.files()
        .list(q=query, fields="files(id, name)", orderBy="name", supportsAllDrives=True, includeItemsFromAllDrives=True)
        .execute()
    )
    files = results.get("files", [])

    entries = []
    for f in files:
        date_str = f["name"].replace(".md", "")
        if since_date and date_str < since_date:
            continue
        raw = _download_file(service, f["id"])
        entries.append({"date": date_str, "content": raw, "tags": _extract_tags(raw)})

    return entries


# ── low-level Drive helpers ──────────────────────────────────────────────────

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
