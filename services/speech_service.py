"""
Speech-to-text via OpenAI's gpt-4o-mini-transcribe model.

The app records browser audio with MediaRecorder and posts it as a blob. OpenAI
handles automatic language detection well for mixed English and Mandarin notes,
so the server can send the file through without codec-specific configuration.
"""

import io
import os

from openai import OpenAI


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Transcribe browser-recorded audio to text.

    The speaker may switch between English and Mandarin Chinese mid-note.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=api_key)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = _filename_for_mime(mime_type)

    transcript = client.audio.transcriptions.create(
        model="gpt-4o-mini-transcribe",
        file=audio_file,
        response_format="text",
        prompt=(
            "The speaker may alternate between English and Mandarin Chinese. "
            "Transcribe naturally, preserve the spoken language, and include "
            "punctuation."
        ),
    )
    return transcript.strip()


def _filename_for_mime(mime_type: str) -> str:
    mime = (mime_type or "").lower()
    if "ogg" in mime:
        return "recording.ogg"
    if "mp4" in mime or "aac" in mime:
        return "recording.m4a"
    if "mpeg" in mime or "mp3" in mime:
        return "recording.mp3"
    if "wav" in mime:
        return "recording.wav"
    return "recording.webm"
