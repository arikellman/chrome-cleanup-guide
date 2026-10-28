"""Browser-based scraping of Yahoo Fantasy's own website, using the
user's own logged-in browser session (via Playwright + a saved
`browser_state.json`), instead of Yahoo's official Fantasy Sports API.

Why this package exists: Yahoo moved the Fantasy Sports API behind a
formal application/approval process and revoked this app's previously-
working API access -- confirmed via extensive live debugging, every
endpoint now 403s "This application is not authorized to perform this
action", even after a fresh OAuth relogin. `app/yahoo/` (the API client)
and `app/jobs/daily.py` / `app/jobs/backfill.py` (the API-based
orchestration) are left in place, dormant, in case Yahoo ever restores
API access -- this package is a parallel, independent path to the exact
same `app/db/schema.sql` tables.

Modules:
  - browser.py    Playwright lifecycle: one-time interactive login
                   (`launch_persistent_session`) and headless page fetches
                   thereafter (`fetch_page`), raising `NeedsReloginError`
                   when the saved session has expired/been revoked.
  - parse.py       HTML -> plain dict/list parsers for the standings,
                   draft-results, and transactions pages (team roster
                   page is stubbed -- see its docstring).
  - identity.py    Bridges scraped identifiers (numeric team_id, stat
                   display names) with the existing team_key/stat_id
                   identity space so a season pulled via scraping lines
                   up with any already-collected API-era history.
  - season_nav.py  Walks Yahoo's `gotoseason` form back through prior
                   seasons, caching each season's resolved numeric
                   league_id.
  - jobs.py        Orchestrates the above into the existing DB tables via
                   app/db/database.py's upsert helpers -- no new/parallel
                   tables.

UNVERIFIED LIVE: this sandbox has no network access to Yahoo, so nothing
that actually drives a browser against a real, logged-in
baseball.fantasysports.yahoo.com session has been exercised end-to-end.
Everything here was built from (a) careful reading of 5 real saved pages
(View Page Source exports of this league's actual pages) and (b)
Playwright's documented API. The four parsers in parse.py were validated
against those real saved files (see each function's docstring for exact
numbers); browser.py, season_nav.py's `resolve_season_league_id`, and the
fetch/pagination orchestration in jobs.py could NOT be -- run `python -m
app scrape-auth` for real and watch the first scrape closely.
"""
