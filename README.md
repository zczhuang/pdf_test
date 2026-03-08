# Journal & Knowledge Management System

A personal journaling app for daily end-of-day reflections across **Work**, **Life**, and **Faith**.

Voice-first UI → Google Drive storage → Weekly summaries via Claude AI.

---

## How It Works

1. Open the web app each evening
2. Tap the mic button and speak your reflection (or type it)
3. Select a category: Work, Life, or Faith
4. Optionally attach a YouTube link or image
5. Hit **Save** — the entry lands in your Google Drive as a Markdown file
6. Every Sunday, Claude generates a weekly summary and saves it to Drive

---

## Architecture

```
Browser (voice recorder + UI)
    ↓  HTTPS
Flask on Cloud Run
    ├── OpenAI gpt-4o-mini-transcribe (voice → text)
    ├── YouTube Data API v3          (link enrichment)
    ├── Google Drive API             (storage)
    └── Claude API (claude-opus-4-6) (weekly summaries)

Cloud Scheduler → POST /summarize   (every Sunday 9 PM UTC)
```

Drive folder layout:
```
Journal/              ← share this folder with your service account
  entries/
    2026-03-08.md
    2026-03-09.md
  media/
    2026-03-08-voice.webm
  summaries/
    weekly/
      2026-W10.md
```

---

## Setup

### 1. GCP Project

1. Create a GCP project at [console.cloud.google.com](https://console.cloud.google.com)
2. Enable these APIs:
   - Google Drive API
   - YouTube Data API v3 _(optional — for link titles)_
   - Cloud Run API
   - Cloud Scheduler API

### 2. Service Account

```bash
# Create a service account
gcloud iam service-accounts create journal-sa \
  --display-name="Journal Service Account"

# Download a key file
gcloud iam service-accounts keys create service-account-key.json \
  --iam-account=journal-sa@YOUR_PROJECT.iam.gserviceaccount.com
```

Note the service account email (e.g. `journal-sa@your-project.iam.gserviceaccount.com`).

### 3. Google Drive Folder

1. Create a folder in your Google Drive called **Journal**
2. Share it with the service account email as **Editor**
3. Copy the folder ID from its URL:
   `https://drive.google.com/drive/folders/`**`THIS_IS_YOUR_FOLDER_ID`**

### 4. Environment Variables

```bash
cp .env.example .env
# Edit .env and fill in:
#   ANTHROPIC_API_KEY
#   OPENAI_API_KEY
#   GOOGLE_APPLICATION_CREDENTIALS  (path to service-account-key.json)
#   DRIVE_FOLDER_ID
#   YOUTUBE_API_KEY                  (optional)
#   FLASK_SECRET_KEY
```

### 5. Run Locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
# Open http://localhost:8080
```

---

## Deploy to Cloud Run

### Build & push container

```bash
gcloud auth configure-docker
docker build -t gcr.io/YOUR_PROJECT/journal .
docker push gcr.io/YOUR_PROJECT/journal
```

### Deploy

```bash
gcloud run deploy journal \
  --image gcr.io/YOUR_PROJECT/journal \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=sk-ant-...,DRIVE_FOLDER_ID=...,FLASK_SECRET_KEY=... \
  --set-secrets GOOGLE_APPLICATION_CREDENTIALS=journal-sa-key:latest
```

> Tip: use [Secret Manager](https://cloud.google.com/secret-manager) for all secrets instead of `--set-env-vars`.

### Set up weekly scheduler (Cloud Scheduler)

```bash
gcloud scheduler jobs create http weekly-summary \
  --location us-central1 \
  --schedule "0 21 * * 0" \
  --uri "https://YOUR_CLOUD_RUN_URL/summarize" \
  --message-body "{}" \
  --headers "Content-Type=application/json" \
  --http-method POST
```

This runs every Sunday at 9 PM UTC.

---

## Generate a Summary Manually

From the UI, click **"Generate this week's summary"**.

Or from the command line:

```bash
# Current week
python -m scheduler.summarize

# Specific week
python -m scheduler.summarize --week-start 2026-03-02
```

---

## Costs (estimated)

| Service | Est. monthly cost |
|---|---|
| Cloud Run (personal use) | Free tier / ~$0 |
| OpenAI gpt-4o-mini-transcribe | usage-based |
| Claude API (claude-opus-4-6) | ~$0.10–0.50/month (weekly summaries) |
| Google Drive API | Free |
| YouTube Data API | Free (10K quota units/day) |
| Cloud Scheduler | Free (≤3 jobs) |
| **Total** | **~$0–2/month** |
