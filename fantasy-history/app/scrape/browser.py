"""Playwright browser lifecycle for scraping Yahoo Fantasy's website using
the user's own logged-in session.

No Yahoo API, no OAuth client_id/secret -- the only "credential" this app
holds for the scraping path is the saved browser session state
(`data/browser_state.json`, gitignored and 0600-permissioned exactly like
tokens.json -- see app/config.py). Getting that session requires a real
interactive login (including any 2FA challenge Yahoo issues), which is
why `launch_persistent_session` below is headful/interactive and
one-time, while `fetch_page` afterwards is headless and unattended.

UNVERIFIED LIVE: nothing in this module has been exercised against a real
Yahoo login or a real browser launch -- this sandbox has no network
access to Yahoo (and no display for a headful browser). It's built
directly from Playwright's documented sync API. Run `python -m app
scrape-auth` for real and watch it closely the first time; if Playwright
itself needs `playwright install chromium` run first, that will surface
here as a clear error rather than a silent hang.
"""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Any

from app import config as cfg

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_URL = "https://login.yahoo.com"


class NeedsReloginError(RuntimeError):
    """Raised when a page navigation lands on login.yahoo.com instead of
    the requested Yahoo Fantasy page -- the saved browser_state.json
    session has expired or been revoked. There is no predictable expiry
    (observed to be weeks-to-months, not a fixed interval) -- this
    exception is how the app is meant to surface that clearly rather than
    fail with a confusing downstream parse error. Re-run:
    python -m app scrape-auth
    """


def _is_login_redirect(url: str) -> bool:
    return "login.yahoo.com" in url


def _require_state_path() -> Path:
    if not cfg.BROWSER_STATE_PATH.exists():
        raise NeedsReloginError(
            f"No saved browser session ({cfg.BROWSER_STATE_PATH} missing). "
            "Run: python -m app scrape-auth"
        )
    return cfg.BROWSER_STATE_PATH


def launch_persistent_session(start_url: str = DEFAULT_LOGIN_URL) -> None:
    """One-time interactive headful login: opens a real, visible browser
    window, lets the human log into Yahoo (including any 2FA challenge --
    the reason this app can't do a fully unattended login the way OAuth's
    refresh token allowed), waits for the human to confirm they're done,
    then saves the authenticated session to browser_state.json for every
    future headless `fetch_page`/`paginate_by_click` call to reuse.
    """
    from playwright.sync_api import sync_playwright

    cfg.ensure_data_dir()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(start_url)
        print(f"\nA browser window opened to {start_url}.")
        print("Log into Yahoo in that window (complete any 2FA challenge), then optionally")
        print("navigate to your league to confirm you're really in.")
        input("Press Enter here once you're logged in... ")
        context.storage_state(path=str(cfg.BROWSER_STATE_PATH))
        browser.close()
    os.chmod(cfg.BROWSER_STATE_PATH, stat.S_IRUSR | stat.S_IWUSR)
    logger.info("Saved browser session state to %s", cfg.BROWSER_STATE_PATH)
    print(f"Saved. Future scrape-season runs will reuse {cfg.BROWSER_STATE_PATH} headlessly.")


def fetch_page(
    url: str,
    *,
    method: str = "get",
    form_data: dict[str, Any] | None = None,
    wait_selector: str | None = None,
) -> str:
    """Headlessly fetches one page's fully-rendered HTML using the saved
    browser_state.json session. Raises NeedsReloginError if Yahoo
    redirects to its login page instead of serving the requested page.

    Confirmed against a real live run: waiting for "networkidle" times out
    on these pages -- Yahoo Sports pages carry constant background
    analytics/ad beacons (comscore, rapid_p, etc., visible throughout the
    saved page sources) that never let the network go fully quiet, so
    Playwright's networkidle (0 connections for 500ms) effectively never
    fires. Wait for "domcontentloaded" instead (fires as soon as the HTML
    itself is parsed, independent of ongoing background requests) and rely
    on `wait_selector` -- which callers should always pass -- as the real
    "is the content I actually need here yet" signal.
    """
    from playwright.sync_api import sync_playwright

    state_path = _require_state_path()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        try:
            if method == "post":
                resp = page.request.post(url, form=form_data or {})
                location = resp.headers.get("location")
                page.goto(location or url, wait_until="domcontentloaded")
            else:
                page.goto(url, wait_until="domcontentloaded")
            if _is_login_redirect(page.url):
                raise NeedsReloginError(
                    f"Redirected to Yahoo login while fetching {url}. "
                    "Session expired or was revoked -- run: python -m app scrape-auth"
                )
            if wait_selector:
                # Confirmed against a real live run: the transactions
                # table shell is present in the DOM almost immediately but
                # stays hidden/empty until its rows are populated by a
                # follow-up request -- so the default "visible" wait state
                # on the bare table selector times out even though the
                # page is working fine. A timeout here is NOT necessarily
                # an error: it can legitimately mean "this page has no
                # matching content" (e.g. a season with zero transactions
                # so far) -- fall through and return whatever HTML exists
                # rather than failing the whole fetch, and let the caller's
                # parser (which returns an empty list on no rows) be the
                # one to decide that's fine.
                try:
                    page.wait_for_selector(wait_selector, timeout=20000)
                except Exception:  # noqa: BLE001 - see comment above
                    logger.warning(
                        "fetch_page: wait_selector %r never appeared/became visible on %s "
                        "(may just mean no matching content) -- returning current page content",
                        wait_selector, url,
                    )
            html = page.content()
        finally:
            browser.close()
    return html


def run_with_page(fn):
    """Opens a headless browser+context (with the saved session) and
    calls `fn(page)`, returning its result -- for operations that need
    finer control than a single fetch_page call, e.g. the gotoseason
    POST-then-follow-redirect sequence in
    app.scrape.season_nav.resolve_season_league_id, which needs a real
    Playwright `Page` object to call `page.request.post` / `page.goto` on
    directly. Raises NeedsReloginError if the page ends up on a login
    redirect once `fn` returns.

    UNVERIFIED LIVE -- see module docstring.
    """
    from playwright.sync_api import sync_playwright

    state_path = _require_state_path()
    with sync_playwright() as p:
        browser_instance = p.chromium.launch(headless=True)
        context = browser_instance.new_context(storage_state=str(state_path))
        page = context.new_page()
        try:
            result = fn(page)
            if _is_login_redirect(page.url):
                raise NeedsReloginError(
                    "Redirected to Yahoo login. Session expired or was revoked -- "
                    "run: python -m app scrape-auth"
                )
            return result
        finally:
            browser_instance.close()


def paginate_by_click(
    url: str,
    *,
    row_selector: str,
    next_link_text: str = "Next 25",
    max_pages: int = 200,
) -> list[str]:
    """Fallback pagination for AJAX-driven pages -- confirmed necessary
    for /transactions, since its visible "Next 25" link's href is
    IDENTICAL to the current page's URL (no incrementing `start=`), so a
    plain GET with a bigger `count=`/`start=` can't be assumed to work
    (see app/scrape/jobs.py's scrape_pull_transactions docstring for the
    full decision tree, including the opportunistic URL-param attempts
    tried before falling back to this).

    Clicks the `next_link_text`-labeled link repeatedly, waiting after
    each click for the first row matching `row_selector` to change text
    (so we don't scrape/return the same page twice), and returns one HTML
    snapshot per page (including the first, pre-click page). Stops when
    no more "Next 25" link is found, the row content doesn't change
    within the timeout (defensive against a page that silently doesn't
    update), or `max_pages` is hit.

    UNVERIFIED LIVE -- see module docstring.
    """
    from playwright.sync_api import sync_playwright

    state_path = _require_state_path()
    pages_html: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            if _is_login_redirect(page.url):
                raise NeedsReloginError(
                    f"Redirected to Yahoo login while fetching {url}. "
                    "Session expired or was revoked -- run: python -m app scrape-auth"
                )
            # Confirmed against a real live --all-seasons run: an older
            # season's page can come back with NO matching content at all
            # (structure mismatch, or a genuinely empty transaction
            # history for that season) -- that crashed the whole
            # --all-seasons walk before this was caught, exactly the same
            # class of "timeout isn't necessarily an error" case fetch_page
            # already handles. Return the single (contentless) page rather
            # than raising, so the caller's parser -- which returns an
            # empty list on no rows -- is the one to decide that's fine.
            try:
                page.wait_for_selector(row_selector, timeout=20000)
            except Exception:  # noqa: BLE001 - see comment above
                logger.warning(
                    "paginate_by_click: row_selector %r never appeared/became visible on %s "
                    "(may just mean no matching content) -- returning current page content",
                    row_selector, url,
                )
                return [page.content()]
            for _ in range(max_pages):
                pages_html.append(page.content())
                first_row_before = page.locator(row_selector).first.inner_text()
                next_link = page.get_by_text(next_link_text, exact=True)
                if next_link.count() == 0:
                    break
                next_link.first.click()
                try:
                    page.wait_for_function(
                        "([sel, prev]) => { const el = document.querySelector(sel); "
                        "return el && el.innerText !== prev; }",
                        arg=[row_selector, first_row_before],
                        timeout=15000,
                    )
                except Exception:  # noqa: BLE001 - see docstring: stop rather than loop forever
                    logger.warning("paginate_by_click: row content didn't change after clicking %r; stopping", next_link_text)
                    break
        finally:
            browser.close()
    return pages_html
