"""Thin HTTP client for the Yahoo Fantasy Sports API.

Handles: bearer auth (via oauth.ensure_valid_token), a simple request
throttle, retry-with-backoff on transient failures, one automatic
token-refresh-and-retry on 401, and persisting every raw response body
into raw_responses so parsing bugs can be fixed later without re-fetching.
"""
from __future__ import annotations

import logging
import sqlite3
import time
import urllib.parse
from typing import Any

import requests

from app.db import database
from app.yahoo import oauth

logger = logging.getLogger(__name__)

BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
MIN_REQUEST_INTERVAL = 1.0  # seconds, be polite to Yahoo's API
MAX_ATTEMPTS = 3
TIMEOUT = 30


class YahooAPIError(RuntimeError):
    pass


def build_query_string(query: dict[str, Any]) -> str:
    """requests' default query-string encoding percent-encodes commas
    (out=settings%2Cstandings), which Yahoo's API rejects with a 400
    ("Invalid subresource ... requested") -- it only accepts the literal
    comma in out=settings,standings. Build the query string with commas
    left unescaped so it can be handed to requests as a pre-encoded
    string (bypassing its own re-encoding)."""
    return urllib.parse.urlencode(query, safe=",")


class YahooClient:
    def __init__(self, conn: sqlite3.Connection | None = None):
        self.conn = conn
        self.session = requests.Session()
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        season_year: int | None = None,
        week: int | None = None,
    ) -> dict[str, Any]:
        """GET {BASE_URL}/{path}?format=json, returning the parsed JSON body."""
        url = f"{BASE_URL}/{path.lstrip('/')}"
        query = dict(params or {})
        query.setdefault("format", "json")
        query_string = build_query_string(query)

        force_refresh = False
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                token = oauth.ensure_valid_token(force_refresh=force_refresh)
                self._last_request_at = time.monotonic()
                resp = self.session.get(
                    url,
                    params=query_string,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=TIMEOUT,
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("Request error on %s (attempt %d): %s", path, attempt, exc)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 401 and not force_refresh:
                # Access token may have been rejected early; force one
                # refresh-and-retry before giving up.
                force_refresh = True
                continue

            if resp.status_code >= 500:
                last_error = YahooAPIError(f"{resp.status_code}: {resp.text[:500]}")
                logger.warning("Server error on %s (attempt %d): %s", path, attempt, last_error)
                time.sleep(2 ** attempt)
                continue

            if resp.status_code != 200:
                raise YahooAPIError(f"Yahoo API {resp.status_code} on {path}: {resp.text[:1000]}")

            body = resp.json()
            if self.conn is not None:
                database.save_raw_response(
                    self.conn, path, repr(sorted(query.items())), body, season_year, week
                )
            return body

        raise YahooAPIError(f"Failed to fetch {path} after {MAX_ATTEMPTS} attempts: {last_error}")
