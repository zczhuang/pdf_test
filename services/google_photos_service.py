"""
Google Photos Picker integration for a single-user personal journal app.

This service uses a dedicated OAuth refresh token with the Google Photos Picker
scope to create picker sessions, poll for completion, list selected items, and
download their bytes so they can be copied into Google Drive.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

PHOTOS_SCOPE = ["https://www.googleapis.com/auth/photospicker.mediaitems.readonly"]
PHOTOS_API_BASE = "https://photospicker.googleapis.com/v1"


def is_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID")
        and os.environ.get("GOOGLE_CLIENT_SECRET")
        and os.environ.get("GOOGLE_PHOTOS_REFRESH_TOKEN")
    )


def create_picker_session(max_item_count: int = 10) -> dict[str, Any]:
    payload = {
        "pickingConfig": {
            "maxItemCount": str(max(1, min(max_item_count, 2000))),
        }
    }
    session = _request("POST", "/sessions", json=payload)
    return {
        "id": session["id"],
        "picker_uri": session["pickerUri"],
        "poll_interval_ms": _duration_to_ms(
            session.get("pollingConfig", {}).get("pollInterval", "3s")
        ),
        "timeout_ms": _duration_to_ms(
            session.get("pollingConfig", {}).get("timeoutIn", "180s")
        ),
        "media_items_set": session.get("mediaItemsSet", False),
    }


def get_picker_session(session_id: str) -> dict[str, Any]:
    session = _request("GET", f"/sessions/{session_id}")
    return {
        "id": session["id"],
        "picker_uri": session.get("pickerUri"),
        "poll_interval_ms": _duration_to_ms(
            session.get("pollingConfig", {}).get("pollInterval", "3s")
        ),
        "timeout_ms": _duration_to_ms(
            session.get("pollingConfig", {}).get("timeoutIn", "180s")
        ),
        "media_items_set": session.get("mediaItemsSet", False),
    }


def list_picked_media_items(session_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        params = {"sessionId": session_id}
        if page_token:
            params["pageToken"] = page_token

        response = _request("GET", "/mediaItems", params=params)
        items.extend(response.get("mediaItems", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def download_picked_media_item(item: dict[str, Any]) -> tuple[str, bytes, str]:
    media_file = item.get("mediaFile", {})
    base_url = media_file.get("baseUrl")
    filename = media_file.get("filename") or f"{item.get('id', 'photo')}.bin"
    mime_type = media_file.get("mimeType") or "application/octet-stream"
    item_type = item.get("type")
    metadata = media_file.get("mediaFileMetadata", {})
    video_metadata = metadata.get("videoMetadata", {})

    if not base_url:
        raise RuntimeError("Google Photos item is missing a baseUrl")

    if item_type == "VIDEO" or mime_type.startswith("video/"):
        status = video_metadata.get("processingStatus")
        if status and status != "READY":
            raise RuntimeError(f"Video is not ready yet: {status}")
        download_url = f"{base_url}=dv"
    else:
        download_url = f"{base_url}=d"

    token = _get_access_token()
    response = requests.get(
        download_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    response.raise_for_status()
    return filename, response.content, mime_type


def _request(method: str, path: str, **kwargs) -> dict[str, Any]:
    if not is_configured():
        raise RuntimeError("Google Photos Picker is not configured")

    token = _get_access_token()
    response = requests.request(
        method,
        f"{PHOTOS_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def _get_access_token() -> str:
    client_id = os.environ["GOOGLE_CLIENT_ID"]
    client_secret = os.environ["GOOGLE_CLIENT_SECRET"]
    refresh_token = os.environ["GOOGLE_PHOTOS_REFRESH_TOKEN"]

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=PHOTOS_SCOPE,
    )
    creds.refresh(Request())
    if not creds.token:
        raise RuntimeError("Failed to refresh Google Photos access token")
    return creds.token


def _duration_to_ms(value: str) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", value or "")
    if not match:
        return 3000
    return int(float(match.group(1)) * 1000)
