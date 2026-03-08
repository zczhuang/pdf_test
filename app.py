"""
Journal & Knowledge Management System — Flask backend.

Routes:
  GET  /              → serve the journal UI
  POST /transcribe    → transcribe voice audio, return text
  POST /entry         → save a journal entry to Google Drive
  POST /summarize     → generate weekly summary via Claude (also called by Cloud Scheduler)
  GET  /health        → health check for Cloud Run
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")


# ── helpers ──────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_week_label() -> str:
    now = datetime.now(timezone.utc)
    # ISO week: e.g. "Week of March 3, 2026"
    week_start = now - __import__("datetime").timedelta(days=now.weekday())
    return f"Week of {week_start.strftime('%B %-d, %Y')}"


def _current_week_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%G-W%V")  # e.g. 2026-W10


def _week_start_date() -> str:
    now = datetime.now(timezone.utc)
    week_start = now - __import__("datetime").timedelta(days=now.weekday())
    return week_start.strftime("%Y-%m-%d")


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/transcribe")
def transcribe():
    """
    Receive an audio blob from the browser, return transcribed text.
    Body: multipart/form-data with field 'audio' (blob).
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
    Save a journal entry.

    Accepts multipart/form-data:
      category   (str)  work | life | faith
      text       (str)  reflection text
      youtube_url (str) optional YouTube URL
      audio      (file) optional: save the raw recording to Drive
      image      (file) optional: attach an image
    """
    category = request.form.get("category", "life")
    text = (request.form.get("text") or "").strip()
    youtube_url = (request.form.get("youtube_url") or "").strip()

    if not text:
        return jsonify({"error": "Reflection text is required"}), 400

    date = _today()
    media_links: list[dict] = []

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

    # Upload image to Drive if provided
    if "image" in request.files:
        image_file = request.files["image"]
        image_bytes = image_file.read()
        if image_bytes:
            try:
                from services.drive_service import upload_media
                link = upload_media(
                    f"{date}-{image_file.filename}",
                    image_bytes,
                    image_file.content_type or "image/jpeg",
                )
                media_links.append({"label": image_file.filename, "url": link["url"]})
            except Exception:
                app.logger.exception("Image upload failed — skipping")

    # Enrich YouTube URL
    youtube_title = None
    if youtube_url:
        try:
            from services.youtube_service import enrich_youtube_url
            info = enrich_youtube_url(youtube_url)
            youtube_title = info.get("title", youtube_url)
        except Exception:
            app.logger.exception("YouTube enrichment failed — using raw URL")

    # Write entry to Drive
    try:
        from services.drive_service import write_entry
        drive_url = write_entry(
            date=date,
            category=category,
            text=text,
            youtube_url=youtube_url or None,
            youtube_title=youtube_title,
            media_links=media_links or None,
        )
        return jsonify({"success": True, "drive_url": drive_url, "date": date})
    except Exception as e:
        app.logger.exception("Entry write failed")
        return jsonify({"error": str(e)}), 500


@app.post("/summarize")
def summarize():
    """
    Generate a weekly summary for the current week.
    Called automatically by Cloud Scheduler every Sunday, or manually from the UI.

    Optional JSON body:
      { "week_start": "YYYY-MM-DD" }   ← override which week to summarize
    """
    body = request.get_json(silent=True) or {}
    since_date = body.get("week_start") or _week_start_date()
    week_label = body.get("week_label") or _current_week_label()
    week_iso = body.get("week_iso") or _current_week_iso()

    try:
        from services.drive_service import list_entries, write_summary
        from services.claude_service import generate_weekly_summary

        entries = list_entries(since_date=since_date)
        summary_md = generate_weekly_summary(entries, week_label)
        drive_url = write_summary(week_iso, summary_md)

        return jsonify({"success": True, "drive_url": drive_url, "entries_count": len(entries)})
    except Exception as e:
        app.logger.exception("Summary generation failed")
        return jsonify({"error": str(e)}), 500


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
