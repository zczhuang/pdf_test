"""
Speech-to-text via Google Cloud Speech-to-Text V2.

Uses Chirp 3 with auto decoding for browser MediaRecorder audio. If Chirp 3
returns nothing, falls back to the standard long-form recognizer with explicit
English and Mandarin language detection.
"""

import os

from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech
from google.oauth2 import service_account

_SCOPE = ["https://www.googleapis.com/auth/cloud-platform"]
_LOCATION = "us"
_LANGUAGE_FALLBACKS = ["en-US", "cmn-Hans-CN", "cmn-Hant-TW"]


def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """
    Transcribe browser-recorded audio to text.

    The current mobile app posts MediaRecorder blobs, usually WebM/Opus on
    Android Chrome. V2 auto decoding handles container/codec detection better
    than the older V1 manual encoding path.
    """
    _ = mime_type  # V2 auto decoding handles the actual media type.

    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not credentials_path:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not configured")

    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=_SCOPE,
    )
    project_id = creds.project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("Unable to determine Google Cloud project ID")

    client = SpeechClient(
        credentials=creds,
        client_options=ClientOptions(api_endpoint=f"{_LOCATION}-speech.googleapis.com"),
    )

    transcript = _recognize(
        client=client,
        project_id=project_id,
        audio_bytes=audio_bytes,
        model="chirp_3",
        language_codes=["auto"],
    )
    if transcript:
        return transcript

    return _recognize(
        client=client,
        project_id=project_id,
        audio_bytes=audio_bytes,
        model="long",
        language_codes=_LANGUAGE_FALLBACKS,
    )


def _recognize(
    client: SpeechClient,
    project_id: str,
    audio_bytes: bytes,
    model: str,
    language_codes: list[str],
) -> str:
    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=language_codes,
        model=model,
        features=cloud_speech.RecognitionFeatures(
            enable_automatic_punctuation=True,
        ),
    )

    request = cloud_speech.RecognizeRequest(
        recognizer=f"projects/{project_id}/locations/{_LOCATION}/recognizers/_",
        config=config,
        content=audio_bytes,
    )
    response = client.recognize(request=request)

    parts = [
        result.alternatives[0].transcript.strip()
        for result in response.results
        if result.alternatives and result.alternatives[0].transcript.strip()
    ]
    return " ".join(parts).strip()
