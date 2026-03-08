"""
Speech-to-text via Google Cloud Speech-to-Text API.

Accepts raw audio bytes from the browser's MediaRecorder (WebM/Opus or OGG/Opus)
and returns a transcription string.

Auth: uses Application Default Credentials (same service account JSON as Drive).
"""

import os

from google.cloud import speech
from google.oauth2 import service_account


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Transcribe audio bytes to text.

    Args:
        audio_bytes: Raw audio data from browser MediaRecorder.
        mime_type:   MIME type reported by the browser.
                     Typically "audio/webm;codecs=opus" (Chrome) or
                     "audio/ogg;codecs=opus" (Firefox).

    Returns:
        Transcribed text, or an empty string if nothing was detected.
    """
    creds = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = speech.SpeechClient(credentials=creds)

    encoding, sample_rate = _encoding_for_mime(mime_type)

    audio = speech.RecognitionAudio(content=audio_bytes)
    config = speech.RecognitionConfig(
        encoding=encoding,
        sample_rate_hertz=sample_rate,
        language_code="en-US",
        enable_automatic_punctuation=True,
        model="latest_long",
    )

    response = client.recognize(config=config, audio=audio)

    parts = [
        result.alternatives[0].transcript
        for result in response.results
        if result.alternatives
    ]
    return " ".join(parts).strip()


def _encoding_for_mime(mime_type: str) -> tuple:
    """Map browser MIME types to Google Speech encoding constants."""
    mt = mime_type.lower()
    if "webm" in mt:
        return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS, 48000
    if "ogg" in mt:
        return speech.RecognitionConfig.AudioEncoding.OGG_OPUS, 48000
    if "mp4" in mt or "aac" in mt:
        # Safari records MP4 — fall back to LINEAR16 via re-encode would be needed
        # For simplicity, treat as WEBM_OPUS and let the API handle it
        return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS, 48000
    # Default
    return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS, 48000
