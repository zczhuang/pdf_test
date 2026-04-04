"""
Google OpenID Connect helpers for JournalPal browser login.
"""

from __future__ import annotations

from urllib.parse import urlencode

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_OIDC_SCOPES = ["openid", "email"]


def build_google_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_OIDC_SCOPES),
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    response = requests.post(
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if "id_token" not in payload:
        raise RuntimeError("Google token response did not include an ID token")
    return payload


def verify_google_id_token(token: str, client_id: str, expected_nonce: str) -> dict:
    token_info = id_token.verify_oauth2_token(token, GoogleAuthRequest(), client_id)
    issuer = token_info.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise RuntimeError("Google ID token had an unexpected issuer")

    if expected_nonce and token_info.get("nonce") != expected_nonce:
        raise RuntimeError("Google ID token nonce mismatch")

    return {
        "email": token_info.get("email", ""),
        "email_verified": bool(token_info.get("email_verified")),
        "name": token_info.get("name", ""),
        "picture": token_info.get("picture", ""),
        "sub": token_info.get("sub", ""),
    }
