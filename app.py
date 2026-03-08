"""
Journal & Knowledge Management System — Flask backend.

Routes:
  GET  /              → serve the journal UI
  POST /transcribe    → transcribe voice audio, return text
  POST /entry         → save a journal entry to Google Drive (auto-tagged by LLM)
  POST /summarize     → load or generate a weekly summary via Gemini
  GET  /health        → health check for Cloud Run
"""

import json
import os
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

SUMMARY_KIND_WEEKLY_RECAP = "weekly_recap"
SUMMARY_KIND_PERIOD_REVIEW = "period_review"


# ── helpers ──────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _today_date() -> date:
    return datetime.now(timezone.utc).date()


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _current_week_label() -> str:
    return _week_label_for(_today_date())


def _current_week_iso() -> str:
    return _iso_week_key(_today_date())


def _week_start_date() -> str:
    return _week_start_for(_today_date()).isoformat()


def _week_start_for(anchor_date: date) -> date:
    return anchor_date - timedelta(days=anchor_date.weekday())


def _week_end_for(anchor_date: date) -> date:
    return _week_start_for(anchor_date) + timedelta(days=6)


def _week_label_for(anchor_date: date) -> str:
    return f"Week of {_week_start_for(anchor_date).strftime('%B %-d, %Y')}"


def _iso_week_key(anchor_date: date) -> str:
    iso_year, iso_week, _ = anchor_date.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _month_end_for(anchor_date: date) -> date:
    if anchor_date.month == 12:
        return date(anchor_date.year, 12, 31)
    return date(anchor_date.year, anchor_date.month + 1, 1) - timedelta(days=1)


def _period_window(period: str, anchor_date: date) -> dict:
    today = _today_date()

    if period == "week":
        start = _week_start_for(anchor_date)
        end = min(_week_end_for(anchor_date), today)
        label = _week_label_for(anchor_date)
        period_key = _iso_week_key(anchor_date)
    elif period == "month":
        start = anchor_date.replace(day=1)
        end = min(_month_end_for(anchor_date), today)
        label = anchor_date.strftime("%B %Y")
        period_key = anchor_date.strftime("%Y-%m")
    elif period == "ytd":
        start = anchor_date.replace(month=1, day=1)
        end = min(anchor_date, today)
        label = f"Year to date through {anchor_date.strftime('%B %-d, %Y')}"
        period_key = f"{anchor_date.year}-through-{end.isoformat()}"
    else:
        raise ValueError("Unsupported summary period")

    return {
        "period": period,
        "period_key": period_key,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "label": label,
    }


def _summary_metadata(
    *,
    summary_kind: str,
    period: str,
    period_key: str,
    label: str,
    start_date: str,
    end_date: str,
    entry_count: int,
    entry_fingerprint: str,
    model: str,
) -> dict:
    return {
        "summary_kind": summary_kind,
        "period": period,
        "period_key": period_key,
        "label": label,
        "start_date": start_date,
        "end_date": end_date,
        "entry_count": entry_count,
        "entry_fingerprint": entry_fingerprint,
        "model": model,
    }


def _resolve_summary(
    *,
    summary_kind: str,
    period: str,
    period_key: str,
    label: str,
    start_date: str,
    end_date: str,
    generator,
) -> dict:
    from services.drive_service import (
        get_entry_snapshot,
        get_summary,
        is_summary_fresh,
        write_summary,
    )
    from services.llm_service import get_summary_model

    snapshot = get_entry_snapshot(start_date, end_date, include_content=False)
    saved_summary = get_summary(summary_kind, period, period_key)
    model = get_summary_model()

    if is_summary_fresh(
        saved_summary,
        summary_kind=summary_kind,
        period=period,
        period_key=period_key,
        start_date=start_date,
        end_date=end_date,
        entry_count=snapshot["entry_count"],
        entry_fingerprint=snapshot["entry_fingerprint"],
        latest_entry_modified=snapshot["latest_entry_modified"],
    ):
        return {
            "summary_markdown": saved_summary["content"],
            "drive_url": saved_summary["drive_url"],
            "entries_count": snapshot["entry_count"],
            "summary_source": "existing",
            "summary_period_key": period_key,
            "model": saved_summary.get("metadata", {}).get("model") or model,
        }

    entries = []
    if snapshot["entry_count"] > 0:
        snapshot = get_entry_snapshot(start_date, end_date, include_content=True)
        entries = snapshot["entries"]

    summary_markdown = generator(entries, label)
    metadata = _summary_metadata(
        summary_kind=summary_kind,
        period=period,
        period_key=period_key,
        label=label,
        start_date=start_date,
        end_date=end_date,
        entry_count=snapshot["entry_count"],
        entry_fingerprint=snapshot["entry_fingerprint"],
        model=model,
    )
    drive_url = write_summary(summary_kind, period, period_key, summary_markdown, metadata)

    return {
        "summary_markdown": summary_markdown,
        "drive_url": drive_url,
        "entries_count": snapshot["entry_count"],
        "summary_source": "generated",
        "summary_period_key": period_key,
        "model": model,
    }


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    google_photos_enabled = bool(os.environ.get("GOOGLE_PHOTOS_REFRESH_TOKEN"))
    return render_template("index.html", google_photos_enabled=google_photos_enabled)


@app.get("/gallery")
def gallery():
    return render_template("gallery.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/gallery/dates")
def gallery_dates():
    limit = request.args.get("limit", default=45, type=int) or 45
    limit = max(1, min(limit, 365))

    try:
        from services.drive_service import list_entry_dates

        return jsonify({"success": True, "dates": list_entry_dates(limit=limit)})
    except Exception as e:
        app.logger.exception("Gallery date listing failed")
        return jsonify({"error": str(e)}), 500


@app.get("/api/gallery/day/<date_value>")
def gallery_day(date_value: str):
    try:
        datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    try:
        from services.drive_service import get_entry_day

        day = get_entry_day(date_value)
        if not day:
            return jsonify({"error": "Entry not found"}), 404
        return jsonify({"success": True, "day": day})
    except Exception as e:
        app.logger.exception("Gallery day load failed")
        return jsonify({"error": str(e)}), 500


@app.get("/api/gallery/summary")
def gallery_summary():
    period = (request.args.get("period") or "week").strip().lower()
    anchor_value = (request.args.get("anchor_date") or _today()).strip()

    try:
        anchor_date = _parse_iso_date(anchor_value)
        window = _period_window(period, anchor_date)
    except ValueError:
        return jsonify({"error": "Invalid summary period or date"}), 400

    try:
        from services.llm_service import generate_period_summary

        result = _resolve_summary(
            summary_kind=SUMMARY_KIND_PERIOD_REVIEW,
            period=period,
            period_key=window["period_key"],
            label=window["label"],
            start_date=window["start_date"],
            end_date=window["end_date"],
            generator=lambda entries, label: generate_period_summary(entries, label, period),
        )

        return jsonify(
            {
                "success": True,
                "period": period,
                "anchor_date": anchor_value,
                "label": window["label"],
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                **result,
            }
        )
    except Exception as e:
        app.logger.exception("Gallery summary generation failed")
        return jsonify({"error": str(e)}), 500


@app.post("/google-photos/session")
def create_google_photos_session():
    body = request.get_json(silent=True) or {}
    max_items = int(body.get("max_items", 10))

    try:
        from services.google_photos_service import create_picker_session

        session = create_picker_session(max_item_count=max_items)
        return jsonify({"success": True, **session})
    except Exception as e:
        app.logger.exception("Google Photos session creation failed")
        return jsonify({"error": str(e)}), 500


@app.post("/google-photos/session/status")
def google_photos_session_status():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    try:
        from services.google_photos_service import get_picker_session

        session = get_picker_session(session_id)
        return jsonify({"success": True, **session})
    except Exception as e:
        app.logger.exception("Google Photos session status failed")
        return jsonify({"error": str(e)}), 500


@app.post("/google-photos/session/items")
def google_photos_session_items():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    try:
        from services.google_photos_service import list_picked_media_items

        items = list_picked_media_items(session_id)
        return jsonify({"success": True, "items": items})
    except Exception as e:
        app.logger.exception("Google Photos item listing failed")
        return jsonify({"error": str(e)}), 500


@app.post("/transcribe")
def transcribe():
    """
    Receive an audio blob from the browser, return transcribed text.
    Supports English and Mandarin Chinese (auto-detected).
    """
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    audio_file = request.files["audio"]
    audio_bytes = audio_file.read()
    mime_type = audio_file.content_type or "audio/webm"

    if not audio_bytes:
        return jsonify({"error": "Empty audio file"}), 400

    try:
        from services.speech_service import transcribe_audio
        text = transcribe_audio(audio_bytes, mime_type)
        return jsonify({"text": text})
    except Exception as e:
        app.logger.exception("Transcription failed")
        return jsonify({"error": str(e)}), 500


@app.post("/entry")
def save_entry():
    """
    Save a journal entry. LLM auto-tags the content.

    Accepts multipart/form-data:
      text        (str)  thoughts/reflection text
      media_urls  (str)  JSON array of URLs (YouTube, Google Photos, any link)
      audio               (file) optional: save the raw recording to Drive
      media               (file) optional: attach a photo or video from device
      google_photos_items (str)  optional: JSON array of picked Google Photos items
    """
    text = (request.form.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Please write or record something first"}), 400

    date = _today()
    media_links: list[dict] = []
    media_urls: list[str] = []
    google_photos_items: list[dict] = []

    # Parse media URLs from the form
    raw_media_urls = request.form.get("media_urls", "")
    if raw_media_urls:
        try:
            media_urls = json.loads(raw_media_urls)
        except json.JSONDecodeError:
            pass

    raw_google_photos_items = request.form.get("google_photos_items", "")
    if raw_google_photos_items:
        try:
            google_photos_items = json.loads(raw_google_photos_items)
        except json.JSONDecodeError:
            pass

    # Upload raw audio to Drive if provided
    if "audio" in request.files:
        audio_file = request.files["audio"]
        audio_bytes = audio_file.read()
        if audio_bytes:
            try:
                from services.drive_service import upload_media
                ext = "webm"
                link = upload_media(
                    f"{date}-voice.{ext}",
                    audio_bytes,
                    audio_file.content_type or "audio/webm",
                )
                media_links.append({"label": "Voice recording", "url": link["url"]})
            except Exception:
                app.logger.exception("Audio upload failed — skipping")

    # Upload photo/video to Drive if provided
    media_file = request.files.get("media") or request.files.get("image")
    if media_file:
        try:
            from services.drive_service import upload_media_stream

            link = upload_media_stream(
                f"{date}-{media_file.filename}",
                media_file.stream,
                media_file.content_type or "application/octet-stream",
            )
            media_links.append({"label": media_file.filename, "url": link["url"]})
        except Exception:
            app.logger.exception("Media upload failed — skipping")

    # Import Google Photos selections into Drive media storage
    for item in google_photos_items[:20]:
        try:
            from services.drive_service import upload_media
            from services.google_photos_service import download_picked_media_item

            filename, media_bytes, mime_type = download_picked_media_item(item)
            drive_link = upload_media(f"{date}-{filename}", media_bytes, mime_type)
            media_links.append(
                {"label": f"Google Photos: {filename}", "url": drive_link["url"]}
            )
        except Exception:
            app.logger.exception("Google Photos import failed — skipping item")

    # Enrich YouTube URLs
    enriched_urls: list[dict] = []
    for url in media_urls:
        label = url
        if "youtube.com" in url or "youtu.be" in url:
            try:
                from services.youtube_service import enrich_youtube_url
                info = enrich_youtube_url(url)
                label = info.get("title", url)
            except Exception:
                app.logger.exception("YouTube enrichment failed — using raw URL")
        enriched_urls.append({"label": label, "url": url})

    # Auto-tag the entry with Gemini
    tags: list[str] = []
    try:
        from services.llm_service import tag_entry
        tags = tag_entry(text)
    except Exception:
        app.logger.exception("Auto-tagging failed — saving without tags")

    # Write entry to Drive
    try:
        from services.drive_service import write_entry
        drive_url = write_entry(
            date=date,
            text=text,
            tags=tags,
            media_urls=enriched_urls or None,
            media_links=media_links or None,
        )
        return jsonify({"success": True, "drive_url": drive_url, "date": date, "tags": tags})
    except Exception as e:
        app.logger.exception("Entry write failed")
        return jsonify({"error": str(e)}), 500


@app.post("/summarize")
def summarize():
    """
    Load or generate a weekly summary for the current week.
    """
    body = request.get_json(silent=True) or {}
    since_date = body.get("week_start") or _week_start_date()

    try:
        from services.llm_service import generate_weekly_summary

        anchor_date = _parse_iso_date(since_date)
        week_window = _period_window("week", anchor_date)
        week_label = body.get("week_label") or week_window["label"]
        week_iso = body.get("week_iso") or week_window["period_key"]

        result = _resolve_summary(
            summary_kind=SUMMARY_KIND_WEEKLY_RECAP,
            period="week",
            period_key=week_iso,
            label=week_label,
            start_date=week_window["start_date"],
            end_date=week_window["end_date"],
            generator=lambda entries, label: generate_weekly_summary(entries, label),
        )

        return jsonify({
            "success": True,
            **result,
            "week_label": week_label,
        })
    except Exception as e:
        app.logger.exception("Summary generation failed")
        return jsonify({"error": str(e)}), 500


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
