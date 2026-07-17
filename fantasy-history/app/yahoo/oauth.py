"""Yahoo OAuth2 three-legged flow: one-time browser consent, then silent
refresh forever after using the persisted refresh token.

This is the mechanism that lets the user provide credentials exactly once:
Yahoo's refresh token does not expire with normal use, so every unattended
daily run can mint a fresh access token without any human interaction,
until the user explicitly revokes the app in their Yahoo account settings.
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Any

import requests

from app import config as cfg

AUTHORIZE_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"

# Refresh this many seconds before actual expiry to avoid racing a request.
EXPIRY_SAFETY_MARGIN = 300


class AuthError(RuntimeError):
    """Raised when Yahoo rejects a token exchange/refresh (e.g. revoked access)."""


def build_authorize_url(config: dict[str, Any]) -> str:
    params = {
        "client_id": config["client_id"],
        "redirect_uri": config.get("redirect_uri", cfg.DEFAULT_REDIRECT_URI),
        "response_type": "code",
        "language": "en-us",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def _store_token_response(data: dict[str, Any]) -> dict[str, Any]:
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + float(data.get("expires_in", 3600)),
        "token_type": data.get("token_type", "bearer"),
    }
    # Yahoo may rotate the refresh token; if it didn't send a new one, keep
    # the existing one rather than wiping it out.
    if not tokens["refresh_token"]:
        existing = cfg.load_tokens()
        tokens["refresh_token"] = existing.get("refresh_token")
    cfg.save_tokens(tokens)
    return tokens


def exchange_code(config: dict[str, Any], code: str) -> dict[str, Any]:
    """Trade the one-time authorization code for access + refresh tokens."""
    resp = requests.post(
        TOKEN_URL,
        auth=(config["client_id"], config["client_secret"]),
        data={
            "grant_type": "authorization_code",
            "redirect_uri": config.get("redirect_uri", cfg.DEFAULT_REDIRECT_URI),
            "code": code,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"Token exchange failed ({resp.status_code}): {resp.text}")
    return _store_token_response(resp.json())


def refresh_access_token(config: dict[str, Any], tokens: dict[str, Any]) -> dict[str, Any]:
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise AuthError("No refresh token on file. Run: python -m app auth")
    resp = requests.post(
        TOKEN_URL,
        auth=(config["client_id"], config["client_secret"]),
        data={
            "grant_type": "refresh_token",
            "redirect_uri": config.get("redirect_uri", cfg.DEFAULT_REDIRECT_URI),
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(
            f"Token refresh failed ({resp.status_code}): {resp.text}. "
            "If Yahoo access was revoked, re-run: python -m app auth"
        )
    return _store_token_response(resp.json())


def ensure_valid_token(force_refresh: bool = False) -> str:
    """Return a valid access token, transparently refreshing if needed."""
    config = cfg.load_config()
    tokens = cfg.load_tokens()
    if not tokens.get("refresh_token"):
        raise AuthError("Not authenticated yet. Run: python -m app auth")

    expires_at = tokens.get("expires_at", 0)
    if force_refresh or time.time() >= (expires_at - EXPIRY_SAFETY_MARGIN):
        tokens = refresh_access_token(config, tokens)
    return tokens["access_token"]
