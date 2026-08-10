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
# Confirmed live across FIVE different non-current seasons now (2001,
# 2003, 2004, 2005, AND 2025 -- so this is not just an "ancient seasons"
# quirk, it's every non-current season): the real, working URL always has
# the year as its own path segment immediately before "/b1/" --
# https://baseball.fantasysports.yahoo.com/2025/b1/33324/standings. Only
# the CURRENT season omits it --
# https://baseball.fantasysports.yahoo.com/b1/74647/standings.
#
# The gotoseason POST's redirect was initially trusted to reveal this on
# its own (via base_url_from_redirect below), but confirmed live that it
# lands on the no-year-prefix form even for a season where that form
# serves an empty page -- so base_url is now always CONSTRUCTED with the
# year prefix for any non-current season (see resolve_season_league_id),
# not read off the redirect. base_url_from_redirect/BASE_URL_RE are kept
# only as a diagnostic cross-check (logged if it disagrees), in case this
# rule is ever wrong for some future season.
BASE_URL_RE = re.compile(r"^(https?://[^/]+(?:/\d{4})?/b1/\d+)")


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


def base_url_from_redirect(url: str) -> str | None:
    """Pulls "https://.../[{year}/]b1/{league_id}" (everything up through
    the league_id, WITH any year path segment that preceded "/b1/" kept
    intact) out of a real URL Yahoo redirected to, or None if it doesn't
    match. This -- not a reconstructed template -- is what every scraped
    page URL for that season must be built from, since whether a given
    season's URL carries a year prefix is confirmed to vary (see
    BASE_URL_RE's comment above) and must never be assumed."""
    m = BASE_URL_RE.match(url)
    return m.group(1) if m else None


def resolve_season_league_id(
    page: Any, form_action: str, seasonspec_value: str, season_year: int, sport_path: str
) -> tuple[str, str]:
    """Drives a real Playwright Page through the `gotoseason` POST and
    returns (league_id, base_url) for that season. league_id is parsed out
    of wherever Yahoo redirects to. base_url is CONSTRUCTED as
    f"https://baseball.fantasysports.yahoo.com/{season_year}/{sport_path}/
    {league_id}" -- confirmed live across 5 different non-current seasons
    that this year-prefixed form is the one that actually serves content,
    NOT whatever the gotoseason redirect's own URL looks like (confirmed
    live that it can land on the no-year-prefix form even when that form
    serves an empty page for that season). If the redirect's own URL
    disagrees with this constructed base_url, that's logged as a
    diagnostic warning (not an error) in case this rule is ever wrong for
    some future season -- but the constructed, confirmed-working form is
    what's trusted and returned.

    Implemented per Playwright's documented `page.request.post` +
    `page.goto` API: POST the form data, follow the `Location` header if
    Yahoo responds with a redirect (the expected case for a "POST that
    changes what season you're looking at"), or just navigate to whatever
    URL the response actually came from if there's no explicit redirect
    header (defensive fallback in case Yahoo instead serves the
    destination page directly as a 200).
    """
    resp = page.request.post(form_action, form={"seasonspec": seasonspec_value})
    location = resp.headers.get("location")
    page.goto(location or resp.url)
    league_id = league_id_from_url(page.url)
    if league_id is None:
        raise RuntimeError(f"Could not extract a league_id from the URL after gotoseason POST: {page.url}")

    base_url = f"https://baseball.fantasysports.yahoo.com/{season_year}/{sport_path}/{league_id}"
    redirect_base_url = base_url_from_redirect(page.url)
    if redirect_base_url and redirect_base_url != base_url:
        logger.warning(
            "gotoseason redirect for season %s landed on %r, which disagrees with the constructed "
            "year-prefixed base_url %r -- using the constructed one (confirmed live to be the one "
            "that actually serves content), but flagging the mismatch in case the rule is wrong here.",
            season_year, redirect_base_url, base_url,
        )
    return league_id, base_url


def resolve_and_cache_season_league_id(
    config: dict[str, Any], season_year: int, sport_path: str = "b1"
) -> dict[str, str] | None:
    """Higher-level, cached version of resolve_season_league_id: looks in
    `config["scraped_season_league_ids"]` first, short-circuits (no POST
    needed at all) when `season_year` is the currently configured season,
    and otherwise drives a real page through the league home page's
    `gotoseason` form -- saving the result into `config` (and persisting
    it via `cfg.save_config`) either way, so the POST is done at most once
    per season ever.

    Returns {"league_id": ..., "base_url": ...} (base_url is the real
    confirmed URL prefix for that season -- see resolve_season_league_id's
    docstring for why this, not a reconstructed template, is what every
    page fetch for that season must be built from), or None if
    `season_year` isn't one of the options Yahoo actually offers for this
    league (e.g. we've walked back past the league's first season) --
    callers (see app/__main__.py's `--all-seasons`) treat that as "stop
    walking back", not as an error.
    """
    cache = config.setdefault("scraped_season_league_ids", {})
    key = str(season_year)
    if key in cache:
        cached = cache[key]
        if isinstance(cached, dict):
            return cached
        # Self-heal a legacy cache entry: an earlier version of this cache
        # stored a bare league_id string instead of {"league_id",
        # "base_url"} -- confirmed against a real data/config.json left
        # over from before base_url was added (this file is gitignored, so
        # old cached state outlives whatever code wrote it). The only code
        # path that could have written a bare string was the
        # current-season short-circuit below, which always used the
        # no-year-prefix default template -- safe to reconstruct here.
        migrated = {
            "league_id": cached,
            "base_url": _league_home_url(cached, config.get("yahoo_web_sport_path", "b1")),
        }
        cache[key] = migrated
        cfg.save_config(config)
        return migrated

    current_league_id = config.get("yahoo_web_league_id")
    current_season_year = config.get("yahoo_web_current_season_year")
    if not current_league_id:
        raise RuntimeError(
            "No current-season league configured yet. Run: python -m app scrape-auth"
        )
    if current_season_year == season_year:
        result = {"league_id": current_league_id, "base_url": _league_home_url(current_league_id, sport_path)}
        cache[key] = result
        cfg.save_config(config)
        return result

    home_url = _league_home_url(current_league_id, sport_path)

    def _do(page: Any) -> tuple[str, str] | None:
        page.goto(home_url, wait_until="domcontentloaded")
        page.wait_for_selector("select[name='seasonspec']", timeout=20000)
        html = page.content()
        slug, options = extract_season_slug(html)
        if not config.get("yahoo_web_season_slug"):
            config["yahoo_web_season_slug"] = slug
        option_value = options.get(season_year)
        if option_value is None:
            logger.info("Season %s has no gotoseason option for this league -- stopping walk-back", season_year)
            return None
        form_action = gotoseason_form_action(html)
        return resolve_season_league_id(page, form_action, option_value, season_year, sport_path)

    resolved = browser.run_with_page(_do)
    if resolved is None:
        return None
    league_id, base_url = resolved
    result = {"league_id": league_id, "base_url": base_url}
    cache[key] = result
    cfg.save_config(config)
    return result
