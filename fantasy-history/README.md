# Fantasy Baseball History

Yahoo only shows you a snapshot of *right now* -- it doesn't let you see how
your league's standings or stats changed day to day over the season, and it
hides past-season history entirely. This app pulls your team and league
results, keeps a day-by-day snapshot of everything (standings, rank, every
stat category's running total) in a local SQLite database, and serves a
local, interactive dashboard so you can see trends build up over the season
and explore/filter every stat it has collected. It can also backfill prior
seasons of the same league, since that history still exists even though
Yahoo's own UI hides it.

## Why this pivoted to browser-based scraping

This app originally used Yahoo's official Fantasy Sports API (OAuth2). Yahoo
has since moved that API behind a formal application/approval process and
revoked this app's previously-working access -- every endpoint now returns
403 "This application is not authorized to perform this action", confirmed
even after a completely fresh OAuth login. Rather than wait on Yahoo's
approval process, the app now scrapes Yahoo Fantasy's own website directly,
using a real browser (via [Playwright](https://playwright.dev/)) signed into
your own normal, already-logged-in Yahoo session. `app/yahoo/` (the old API
client) and `app/jobs/daily.py`/`app/jobs/backfill.py` (its orchestration)
are left in the codebase untouched, dormant, in case Yahoo ever restores API
access -- see their module docstrings.

## How it works (browser scraping)

- `python -m app scrape-auth` opens a real, visible browser window **once**
  for you to log into Yahoo normally (including any 2FA challenge -- the same
  reason a fully unattended login isn't possible, whether via the old OAuth
  flow or this one). That session is then saved to `data/browser_state.json`
  and reused headlessly (no visible browser window) by every future scrape.
- All data still lives in `data/fantasy.db` (SQLite) -- scraping writes into
  the exact same tables the API path used, so switching between them (or
  back, if Yahoo restores API access) doesn't fragment your history.
  `data/config.json`, `data/tokens.json` (dormant), and
  `data/browser_state.json` are all gitignored and permission-locked
  (0600/0700) since `browser_state.json` holds live session cookies -- treat
  it with the same care as a password.
- **The saved session eventually expires or gets revoked** -- observed to be
  weeks to months out, not a predictable fixed interval. When that happens,
  any scrape attempt raises a clear `NeedsReloginError` (rather than failing
  with a confusing downstream parse error) telling you to re-run
  `python -m app scrape-auth`.
- `python -m app serve` runs a small Flask dashboard AND an in-process
  scheduler that pulls once a day (default 07:30) and automatically catches
  up if your computer was asleep or off when the scheduled time passed --
  unchanged from before.

**IMPORTANT caveat for whoever runs this for real:** the actual Playwright
browser automation against a live, logged-in Yahoo session has NOT been
exercised end-to-end anywhere this code was written (no network access to
Yahoo in that environment). The four HTML parsers (`app/scrape/parse.py`)
were validated against real saved Yahoo pages and are unit-tested against
fixtures mirroring that confirmed structure -- but `scrape-auth`'s browser
launch, the headless `fetch_page`/`paginate_by_click` calls, and the
`gotoseason` season walk-back all need to be run for real and watched
closely the first time. If Yahoo's page structure has shifted since, expect
to need small fixes in `app/scrape/parse.py` guided by whatever HTML actually
comes back (nothing here is a black box -- every parser function's docstring
says exactly what it assumes and why).

## One-time setup

1. Install dependencies, including the Playwright browser binary:
   ```
   cd fantasy-history
   pip install -r requirements.txt
   playwright install chromium
   ```

2. Log in once, interactively:
   ```
   python -m app scrape-auth
   ```
   A real browser window opens to Yahoo's login page. Log in (complete any
   2FA challenge), then come back to the terminal and press Enter. Your
   session is saved to `data/browser_state.json` for every future scrape to
   reuse headlessly.

3. Tell the app which league to track by editing `data/config.json`:
   ```json
   {
     "yahoo_web_league_id": "74647",
     "yahoo_web_current_season_year": 2026
   }
   ```
   `yahoo_web_league_id` is the numeric id in your league's own URL (the
   `74647` in `baseball.fantasysports.yahoo.com/b1/74647/...`);
   `yahoo_web_current_season_year` is the season that id currently points to.
   (`yahoo_web_sport_path` defaults to `"b1"`, baseball -- only change it if
   you're pointing this at a different Yahoo fantasy sport.)

4. Recover full history for every season Yahoo has for this league:
   ```
   python -m app scrape-season --all-seasons
   ```
   This walks backward one season at a time via Yahoo's own "change season"
   dropdown, scraping standings, draft results, and transactions for each,
   and stops automatically as soon as it hits a season this league doesn't
   have (rather than failing the whole run). It's resumable: each season's
   resolved numeric league_id is cached in `data/config.json` so re-running
   doesn't repeat that step for seasons already found.

5. Start the dashboard (leave this running -- it pulls daily and catches up
   automatically after sleep/reboot):
   ```
   python -m app serve
   ```
   Then open http://localhost:8765

## CLI reference

| Command | What it does |
|---|---|
| `python -m app scrape-auth` | One-time interactive Yahoo login for scraping |
| `python -m app scrape-season 2022` | Scrape one season (walks `gotoseason` if needed) |
| `python -m app scrape-season --all-seasons` | Recover every season's history, back to 2001 |
| `python -m app pull` | Run one manual pull of the current season right now |
| `python -m app backfill --all` | Recover every known season's history |
| `python -m app backfill --season 2022` | Recover a single season |
| `python -m app serve [--host] [--port]` | Run the dashboard + daily scheduler |
| `python -m app status` | Show config/session/database status |
| `python -m app diagnose` | Per-category data coverage + raw standings shape, for debugging gaps |
| `python -m app auth` | (Dormant) one-time OAuth setup for the old API path |

## Dashboard

Pick your team from the "My team..." selector in the header (remembered across
visits) to have it highlighted throughout every table.

The dashboard adapts to your league's scoring type automatically (each season
is checked independently, so a switch from head-to-head to roto, or vice
versa, is handled correctly per year):

**Head-to-head leagues** get six tabs, all filterable/sortable, no page reloads:

- **Standings** -- current (or any-date) standings table, plus a rank-over-time
  chart across the season with a clickable legend to isolate teams.
- **Matchups** -- weekly results filterable by week/team; click a row to expand
  the category-by-category breakdown.
- **Head-to-Head** -- all-time win/loss record between every pair of managers
  (tracked by Yahoo manager GUID, so it follows people across seasons/renamed
  teams), with a toggle for a single season vs. combined history.
- **Categories** -- a day-by-day trend chart of each stat category's running
  season total per team (pick HR, ERA, etc. from the dropdown to see it climb
  or dip over the season), plus per-team win rate across matchups so far as a
  bar chart and full table. Counting stats (HR, RBI, SB, ...) show the whole
  season immediately after backfill, reconstructed day by day; rate stats
  (ERA, WHIP, OBP, ...) can't be reconstructed that way (a day's ratio isn't
  summable into a season ratio) so those only show history from whenever
  this app started running.
- **Transactions** -- add/drop/trade log, filterable by team, type, and player
  name search.
- **History** -- one row per season showing final standings once Yahoo marks
  the season finished.

**Rotisserie leagues** don't have weekly matchups, so the Matchups and
Head-to-Head tabs are hidden automatically, and the Standings tab gains two
extra tables that reconstruct Yahoo's own roto standings page from the daily
stat snapshots:

- **Overall Stats** -- each team's raw cumulative total per category
  (Batting/Pitching grouped, matching Yahoo's layout), ranked by total points.
- **Overall Points** -- each category converted to roto points (best team in
  a category gets one point per team in the league, ties split the points),
  a Total column, and a day-over-day Change column so you can see who gained
  or lost ground since the last pull.

Counting stats (HR, RBI, SB, W, SV, ...) match Yahoo's own points exactly.
Rate stats (ERA, WHIP, OBP) may differ slightly near the innings/at-bat
qualifier minimum Yahoo applies to those categories, since that threshold
rule isn't exposed anywhere in the league settings the API returns and so
isn't modeled here -- see `app/roto.py` for details.

## If you'd rather not leave `serve` running

`serve` includes its own scheduler so this is the simplest option, but if you
prefer an OS-level scheduler instead, run `python -m app pull` on a schedule
and separately run `python -m app serve` (or just query the dashboard) when
you want to look at it:

**macOS/Linux (cron)** -- `crontab -e`:
```
30 7 * * * cd /path/to/fantasy-history && /usr/bin/python3 -m app pull >> data/logs/cron.log 2>&1
```

**Windows (Task Scheduler)** -- create a daily trigger running:
```
python -m app pull
```
with "Start in" set to the `fantasy-history` folder.

## Troubleshooting

- **A pull/backfill/scrape-season command fails with a `NeedsReloginError`**
  -- your saved browser session (`data/browser_state.json`) has expired or
  been revoked. There's no predictable expiry (observed to be weeks to
  months, not a fixed interval) -- this is the app surfacing that clearly
  instead of failing with a confusing downstream parse error. Re-run:
  `python -m app scrape-auth`.
- **`status` shows "Browser session: False"** -- you haven't run
  `python -m app scrape-auth` yet (or it didn't complete).
- **A field looks wrong in the dashboard** -- for scraped data, check
  `app/scrape/parse.py`'s docstrings first; each parser documents exactly
  what real page structure it was validated against. For anything still
  coming from the (dormant) API path, every raw API response is saved
  verbatim in the `raw_responses` table so parsing issues can be fixed
  without re-fetching anything -- see `app/yahoo/parse.py`.
- **(Dormant API path) `status` shows "Authenticated: False"** -- only
  relevant if/when Yahoo restores API access; run `python -m app auth`.
