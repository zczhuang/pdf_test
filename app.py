"""
Journal & Knowledge Management System — Flask backend.

Routes:
  GET  /              → serve the journal UI
  POST /transcribe    → transcribe voice audio, return text
  POST /entry         → save a journal entry to Google Drive (auto-tagged by LLM)
  POST /summarize     → generate weekly summary via Claude
  GET  /health        → health check for Cloud Run
"""

import json
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
    week_start = now - __import__("datetime").timedelta(days=now.weekday())
    return f"Week of {week_start.strftime('%B %-d, %Y')}"


def _current_week_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%G-W%V")


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
      audio       (file) optional: save the raw recording to Drive
      media       (file) optional: attach a photo or video
    """
    text = (request.form.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Please write or record something first"}), 400

    date = _today()
    media_links: list[dict] = []
    media_urls: list[str] = []

    # Parse media URLs from the form
    raw_media_urls = request.form.get("media_urls", "")
    if raw_media_urls:
        try:
            media_urls = json.loads(raw_media_urls)
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
        media_bytes = media_file.read()
        if media_bytes:
            try:
                from services.drive_service import upload_media

                link = upload_media(
                    f"{date}-{media_file.filename}",
                    media_bytes,
                    media_file.content_type or "application/octet-stream",
                )
                media_links.append({"label": media_file.filename, "url": link["url"]})
            except Exception:
                app.logger.exception("Media upload failed — skipping")

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

    # Auto-tag the entry with Claude
    tags: list[str] = []
    try:
        from services.claude_service import tag_entry
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
    Generate a weekly summary for the current week.
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
