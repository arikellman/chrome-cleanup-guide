"""Paths, config.json, and tokens.json/browser_state.json handling.

Everything under data/ is gitignored and kept at restrictive file
permissions since it holds OAuth secrets/tokens (tokens.json, dormant
pending Yahoo API access being restored -- see app/yahoo/) and, for the
current browser-scraping approach, a live logged-in session
(browser_state.json -- see app/scrape/browser.py). Both get the same
0600 treatment.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
CONFIG_PATH = DATA_DIR / "config.json"
TOKENS_PATH = DATA_DIR / "tokens.json"
BROWSER_STATE_PATH = DATA_DIR / "browser_state.json"
DB_PATH = DATA_DIR / "fantasy.db"

DEFAULT_REDIRECT_URI = "https://localhost:8765"
DEFAULT_PULL_TIME = "07:30"
DEFAULT_SPORT_PATH = "b1"  # baseball; see config keys yahoo_web_* below

_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600
_DIR_MODE = stat.S_IRWXU  # 0700


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, _DIR_MODE)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(LOG_DIR, _DIR_MODE)


def _write_json_secure(path: Path, data: dict[str, Any]) -> None:
    """Atomic write (temp file + rename) with 0600 permissions."""
    ensure_data_dir()
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.chmod(tmp_name, _FILE_MODE)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r") as f:
        return json.load(f)


def load_config() -> dict[str, Any]:
    config = _read_json(CONFIG_PATH)
    config.setdefault("redirect_uri", DEFAULT_REDIRECT_URI)
    config.setdefault("pull_time", DEFAULT_PULL_TIME)
    config.setdefault("prior_league_keys", [])
    # Browser-scraping config (app/scrape/) -- independent of the
    # (dormant) API-era client_id/league_key fields above.
    #   yahoo_web_league_id:    current season's numeric league id, e.g.
    #                           "74647" (the "74647" in
    #                           baseball.fantasysports.yahoo.com/b1/74647/...).
    #   yahoo_web_season_slug:  this league's stable history slug used by
    #                           Yahoo's gotoseason form, e.g. "kippahs"
    #                           (see app/scrape/season_nav.py) --
    #                           discovered automatically on first scrape,
    #                           not something to type in by hand.
    #   yahoo_web_sport_path:   the sport's URL path segment, e.g. "b1"
    #                           for baseball.
    #   scraped_season_league_ids: {season_year: league_id} cache built up
    #                           by season_nav.resolve_season_league_id so
    #                           the gotoseason POST is only done once per
    #                           season ever, not once per pull.
    config.setdefault("yahoo_web_league_id", None)
    config.setdefault("yahoo_web_current_season_year", None)
    config.setdefault("yahoo_web_season_slug", None)
    config.setdefault("yahoo_web_sport_path", DEFAULT_SPORT_PATH)
    config.setdefault("scraped_season_league_ids", {})
    return config


def save_config(config: dict[str, Any]) -> None:
    _write_json_secure(CONFIG_PATH, config)


def load_tokens() -> dict[str, Any]:
    return _read_json(TOKENS_PATH)


def save_tokens(tokens: dict[str, Any]) -> None:
    _write_json_secure(TOKENS_PATH, tokens)


def has_credentials() -> bool:
    config = load_config()
    return bool(config.get("client_id") and config.get("client_secret"))


def has_league() -> bool:
    return bool(load_config().get("league_key"))


def has_tokens() -> bool:
    tokens = load_tokens()
    return bool(tokens.get("refresh_token"))


def has_browser_session() -> bool:
    """Whether a one-time `python -m app scrape-auth` login has been done
    and saved -- the scraping-path equivalent of has_tokens() above."""
    return BROWSER_STATE_PATH.exists()
