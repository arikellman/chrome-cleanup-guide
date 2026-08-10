"""Season walk-back via Yahoo's `gotoseason` form.

Confirmed against the real saved league-home page: there's a
`<select name="seasonspec" id="seasonspec">` inside a
`<form action="https://baseball.fantasysports.yahoo.com/b1/74647/gotoseason"
method="post">` with options like `<option value="2026_kippahs">2026
Season</option>` back to 2001. The "_kippahs" suffix is this specific
league's stable history slug -- `extract_season_slug` below pulls it out
of whichever option value it sees first rather than it ever being
hardcoded, since a different league would have a different slug.

The pure HTML-parsing helpers here (`extract_season_slug`,
`gotoseason_form_action`, `league_id_from_url`) are unit-tested against a
hand-crafted fixture mirroring the confirmed real structure.
`resolve_season_league_id` additionally needs a live Playwright `Page`
object driving a real logged-in session, so it is UNVERIFIED LIVE -- see
app/scrape/__init__.py's module docstring.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from app import config as cfg
from app.scrape import browser

logger = logging.getLogger(__name__)

SEASON_OPTION_RE = re.compile(r"^(\d{4})_(.+)$")
LEAGUE_ID_IN_URL_RE = re.compile(r"/b1/(\d+)(?:[/?]|$)")


def _league_home_url(league_id: str, sport_path: str) -> str:
    # Same URL scheme as app.scrape.jobs.league_home_url -- duplicated
    # (rather than imported) to avoid a season_nav<->jobs import cycle,
    # since jobs.py orchestrates season walk-back BY CALLING this module.
    return f"https://baseball.fantasysports.yahoo.com/{sport_path}/{league_id}"


def extract_season_slug(html: str) -> tuple[str, dict[int, str]]:
    """Parses the `seasonspec` <select> out of a league home page.
    Returns (slug, {season_year: option_value}), e.g. ("kippahs",
    {2026: "2026_kippahs", 2025: "2025_kippahs", ...}). Raises ValueError
    if the <select> isn't found (page shape changed, or the page we
    actually landed on was a login/error page rather than the league
    home page -- callers should treat that as needing a fresh login/
    NeedsReloginError rather than retrying blindly).
    """
    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", {"name": "seasonspec"})
    if select is None:
        raise ValueError("seasonspec <select> not found on league home page")
    options: dict[int, str] = {}
    slug = None
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        m = SEASON_OPTION_RE.match(value)
        if not m:
            continue
        year = int(m.group(1))
        options[year] = value
        if slug is None:
            slug = m.group(2)
    if slug is None or not options:
        raise ValueError("no usable season options found in seasonspec <select>")
    return slug, options


def gotoseason_form_action(html: str) -> str:
    """Returns the `gotoseason` form's `action` URL (the POST target)."""
    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", {"name": "seasonspec"})
    if select is None:
        raise ValueError("seasonspec <select> not found on league home page")
    form = select.find_parent("form")
    if form is None or not form.get("action"):
        raise ValueError("gotoseason <form> (with an action) not found around seasonspec <select>")
    return form["action"]


def league_id_from_url(url: str) -> str | None:
    """Pulls the numeric league_id out of a `.../b1/{league_id}/...` URL,
    or None if the URL doesn't match that shape (e.g. we're still on a
    login page)."""
    m = LEAGUE_ID_IN_URL_RE.search(url)
    return m.group(1) if m else None


def resolve_season_league_id(page: Any, form_action: str, seasonspec_value: str) -> str:
    """Drives a real Playwright Page through the `gotoseason` POST and
    returns the numeric league_id parsed out of wherever Yahoo redirects
    to.

    UNVERIFIED LIVE -- see module docstring. Implemented per Playwright's
    documented `page.request.post` + `page.goto` API: POST the form data,
    follow the `Location` header if Yahoo responds with a redirect (the
    expected case for a "POST that changes what season you're looking
    at"), or just navigate to whatever URL the response actually came
    from if there's no explicit redirect header (defensive fallback in
    case Yahoo instead serves the destination page directly as a 200).
    """
    resp = page.request.post(form_action, form={"seasonspec": seasonspec_value})
    location = resp.headers.get("location")
    page.goto(location or resp.url)
    league_id = league_id_from_url(page.url)
    if league_id is None:
        raise RuntimeError(
            f"Could not extract a league_id from the URL after gotoseason POST: {page.url}"
        )
    return league_id


def resolve_and_cache_season_league_id(
    config: dict[str, Any], season_year: int, sport_path: str = "b1"
) -> str | None:
    """Higher-level, cached version of resolve_season_league_id: looks in
    `config["scraped_season_league_ids"]` first, short-circuits to
    `config["yahoo_web_league_id"]` when `season_year` is the currently
    configured season (no POST needed at all), and otherwise drives a
    real page through the league home page's `gotoseason` form -- saving
    the result into `config` (and persisting it via `cfg.save_config`)
    either way, so the POST is done at most once per season ever.

    Returns None if `season_year` isn't one of the options Yahoo actually
    offers for this league (e.g. we've walked back past the league's
    first season) -- callers (see app/__main__.py's `--all-seasons`) treat
    that as "stop walking back", not as an error.

    UNVERIFIED LIVE -- see app/scrape/__init__.py's module docstring.
    """
    cache = config.setdefault("scraped_season_league_ids", {})
    key = str(season_year)
    if key in cache:
        return cache[key]

    current_league_id = config.get("yahoo_web_league_id")
    current_season_year = config.get("yahoo_web_current_season_year")
    if not current_league_id:
        raise RuntimeError(
            "No current-season league configured yet. Run: python -m app scrape-auth"
        )
    if current_season_year == season_year:
        cache[key] = current_league_id
        cfg.save_config(config)
        return current_league_id

    home_url = _league_home_url(current_league_id, sport_path)

    def _do(page: Any) -> str | None:
        page.goto(home_url, wait_until="networkidle")
        html = page.content()
        slug, options = extract_season_slug(html)
        if not config.get("yahoo_web_season_slug"):
            config["yahoo_web_season_slug"] = slug
        option_value = options.get(season_year)
        if option_value is None:
            logger.info("Season %s has no gotoseason option for this league -- stopping walk-back", season_year)
            return None
        form_action = gotoseason_form_action(html)
        return resolve_season_league_id(page, form_action, option_value)

    league_id = browser.run_with_page(_do)
    if league_id is None:
        return None
    cache[key] = league_id
    cfg.save_config(config)
    return league_id
