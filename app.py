"""
Journal & Knowledge Management System — Flask backend.

Routes:
  GET  /              → serve the journal UI
  GET  /login         → Google sign-in screen
  GET  /auth/callback → Google OAuth callback
  POST /transcribe    → transcribe voice audio, return text
  POST /entry         → save a journal entry to Google Drive (auto-tagged by LLM)
  POST /summarize     → load or generate a weekly summary via Gemini
  GET  /health        → health check for Cloud Run
"""

import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlencode, urlsplit

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config.update(
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_ENV", "development") != "development",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

SUMMARY_KIND_WEEKLY_RECAP = "weekly_recap"
SUMMARY_KIND_PERIOD_REVIEW = "period_review"
PUBLIC_ENDPOINTS = {"health", "login", "auth_callback", "logout", "static"}
EXPENSIVE_ROUTE_LIMITS = {
    "/transcribe": (24, 3600),
    "/entry": (60, 3600),
    "/summarize": (24, 3600),
    "/google-photos/session": (24, 3600),
    "/google-photos/session/status": (120, 3600),
    "/google-photos/session/items": (60, 3600),
}
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = Lock()


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


def _auth_client_id() -> str:
    return os.environ.get("GOOGLE_OIDC_CLIENT_ID", "").strip()


def _auth_client_secret() -> str:
    return os.environ.get("GOOGLE_OIDC_CLIENT_SECRET", "").strip()


def _allowed_google_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_GOOGLE_EMAILS", "")
    normalized = raw.replace("\n", ",").replace(";", ",")
    return {item.strip().lower() for item in normalized.split(",") if item.strip()}


def _auth_ready() -> bool:
    return bool(_auth_client_id() and _auth_client_secret() and _allowed_google_emails())


def _current_user() -> dict | None:
    user = session.get("user")
    if not isinstance(user, dict):
        return None

    email = str(user.get("email", "")).strip().lower()
    if not email:
        session.pop("user", None)
        return None

    if email not in _allowed_google_emails():
        session.clear()
        return None

    user["email"] = email
    return user


def _current_user_email() -> str:
    user = _current_user()
    return str(user.get("email", "")).strip().lower() if user else ""


def _request_target() -> str:
    query = request.query_string.decode().strip()
    return f"{request.path}?{query}" if query else request.path


def _sanitize_next_target(target: str | None) -> str:
    if not target:
        return url_for("index")

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return url_for("index")

    if not target.startswith("/") or target.startswith("//"):
        return url_for("index")

    return target


def _public_base_url() -> str:
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return request.host_url.rstrip("/")


def _auth_callback_url() -> str:
    return f"{_public_base_url()}{url_for('auth_callback')}"


def _is_api_request() -> bool:
    return (
        request.path.startswith("/api/")
        or request.path.startswith("/google-photos/")
        or request.path in {"/transcribe", "/entry", "/summarize"}
    )


def _unauthenticated_response():
    login_url = url_for("login", next=_sanitize_next_target(_request_target()))
    if _is_api_request() or request.method != "GET":
        return jsonify({"error": "Authentication required", "login_url": login_url}), 401
    return redirect(login_url)


def _rate_limit_response(limit: int, window_seconds: int):
    return (
        jsonify(
            {
                "error": "Rate limit exceeded",
                "limit": limit,
                "window_seconds": window_seconds,
            }
        ),
        429,
    )


def _apply_rate_limit():
    config = EXPENSIVE_ROUTE_LIMITS.get(request.path)
    if not config:
        return None

    limit, window_seconds = config
    identity = _current_user_email() or request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    bucket_key = f"{request.path}:{identity}"
    now = time.time()

    with _rate_limit_lock:
        bucket = _rate_limit_buckets[bucket_key]
        while bucket and (now - bucket[0]) >= window_seconds:
            bucket.popleft()

        if len(bucket) >= limit:
            app.logger.warning(
                "rate_limit route=%s email=%s remote_addr=%s",
                request.path,
                _current_user_email() or "unknown",
                request.remote_addr or "unknown",
            )
            return _rate_limit_response(limit, window_seconds)

        bucket.append(now)

    return None


def _audit_log(action: str, **details) -> None:
    detail_str = " ".join(f"{key}={value}" for key, value in sorted(details.items()))
    app.logger.info(
        "audit action=%s email=%s path=%s remote_addr=%s %s",
        action,
        _current_user_email() or "anonymous",
        request.path,
        request.remote_addr or "unknown",
        detail_str,
    )


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


@app.before_request
def require_login():
    endpoint = request.endpoint or ""
    if endpoint in PUBLIC_ENDPOINTS or request.path.startswith("/static/"):
        return None

    if not _current_user():
        return _unauthenticated_response()

    return _apply_rate_limit()


@app.context_processor
def auth_template_context():
    return {
        "current_user_email": _current_user_email(),
    }


# ── routes ───────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if _current_user():
        return redirect(_sanitize_next_target(request.args.get("next")))

    next_path = _sanitize_next_target(
        request.form.get("next") if request.method == "POST" else request.args.get("next")
    )
    error_message = request.args.get("error", "").strip()
    info_message = "You need to sign in with your Google account to open this journal."

    if request.method == "POST":
        if not _auth_ready():
            return (
                render_template(
                    "login.html",
                    next_path=next_path,
                    login_ready=False,
                    error_message="Google sign-in is not configured yet.",
                    info_message=info_message,
                ),
                503,
            )

        from services.auth_service import build_google_authorization_url

        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        session["oauth_state"] = state
        session["oauth_nonce"] = nonce
        session["post_login_redirect"] = next_path

        auth_url = build_google_authorization_url(
            client_id=_auth_client_id(),
            redirect_uri=_auth_callback_url(),
            state=state,
            nonce=nonce,
        )
        return redirect(auth_url)

    return render_template(
        "login.html",
        next_path=next_path,
        login_ready=_auth_ready(),
        error_message=error_message,
        info_message=info_message,
    )


@app.get("/auth/callback")
def auth_callback():
    oauth_error = request.args.get("error")
    if oauth_error:
        session.pop("oauth_state", None)
        session.pop("oauth_nonce", None)
        session.pop("post_login_redirect", None)
        return redirect(url_for("login", error=f"Google sign-in was cancelled: {oauth_error}"))

    code = request.args.get("code", "").strip()
    state = request.args.get("state", "").strip()

    expected_state = session.pop("oauth_state", "")
    expected_nonce = session.pop("oauth_nonce", "")
    next_path = _sanitize_next_target(session.pop("post_login_redirect", None))

    if not code or not state or state != expected_state:
        return redirect(url_for("login", error="The Google sign-in session was invalid. Please try again."))

    try:
        from services.auth_service import exchange_code_for_tokens, verify_google_id_token

        tokens = exchange_code_for_tokens(
            code=code,
            client_id=_auth_client_id(),
            client_secret=_auth_client_secret(),
            redirect_uri=_auth_callback_url(),
        )
        user = verify_google_id_token(tokens["id_token"], _auth_client_id(), expected_nonce)
    except Exception as exc:
        app.logger.exception("Google OAuth callback failed")
        return redirect(url_for("login", error=f"Google sign-in failed: {exc}"))

    email = str(user.get("email", "")).strip().lower()
    if not user.get("email_verified") or email not in _allowed_google_emails():
        _audit_log("login_denied", attempted_email=email or "unknown")
        return redirect(url_for("login", error="This Google account is not allowed to access JournalPal."))

    session["user"] = {
        "email": email,
        "name": user.get("name", ""),
        "picture": user.get("picture", ""),
    }
    _audit_log("login_success")
    return redirect(next_path)


@app.get("/logout")
def logout():
    email = _current_user_email()
    session.clear()
    if email:
        app.logger.info("audit action=logout email=%s path=/logout", email)
    return redirect(url_for("login"))

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
        _audit_log(
            "gallery_summary",
            period=period,
            source=result["summary_source"],
            entries=result["entries_count"],
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

        picker_session = create_picker_session(max_item_count=max_items)
        _audit_log("google_photos_session_create", max_items=max_items)
        return jsonify({"success": True, **picker_session})
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

        picker_session = get_picker_session(session_id)
        _audit_log("google_photos_session_status")
        return jsonify({"success": True, **picker_session})
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
        _audit_log("google_photos_session_items", item_count=len(items))
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
        _audit_log("transcribe", audio_bytes=len(audio_bytes))
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
        _audit_log("entry_saved", tag_count=len(tags), media_count=len(media_links) + len(enriched_urls))
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
        _audit_log(
            "weekly_summary",
            source=result["summary_source"],
            entries=result["entries_count"],
            week=week_iso,
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
