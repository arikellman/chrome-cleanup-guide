# Fantasy Baseball History

Yahoo only shows you a snapshot of *right now* -- it doesn't let you see how
your league's standings or stats changed day to day over the season, and it
hides past-season history entirely. This app pulls your team and league
results from Yahoo's official Fantasy Sports API once a day, keeps a
day-by-day snapshot of everything (standings, rank, every stat category's
running total) in a local SQLite database, and serves a local, interactive
dashboard so you can see trends build up over the season and explore/filter
every stat it has collected. It can also backfill prior seasons of the same
league, since Yahoo's API still has that data even though the UI hides it.

You authenticate with Yahoo **once**. After that it runs unattended forever
(until you revoke access in your Yahoo account settings).

## How it works

- Uses Yahoo's official OAuth2 Fantasy Sports API, not screen-scraping.
  Yahoo login requires 2FA for most accounts, which a headless scraper can't
  get through unattended -- OAuth2's refresh token is what actually lets this
  run with no human in the loop.
- All data lives in `data/fantasy.db` (SQLite), plus `data/config.json` and
  `data/tokens.json` for your app credentials and OAuth tokens. That whole
  `data/` folder is gitignored and permission-locked (0600/0700) since it
  holds secrets.
- `python -m app serve` runs a small Flask dashboard AND an in-process
  scheduler that pulls once a day (default 07:30) and automatically catches
  up if your computer was asleep or off when the scheduled time passed.

## One-time setup

1. Install dependencies:
   ```
   cd fantasy-history
   pip install -r requirements.txt
   ```

2. Create a Yahoo Developer app at
   https://developer.yahoo.com/apps/create/
   - App type: **Installed Application** (Confidential Client)
   - Redirect URI: `https://localhost:8765`
   - API Permissions: **Fantasy Sports** (Read)

   Copy the **Client ID** and **Client Secret** it gives you.

3. Run the one-time auth flow:
   ```
   python -m app auth
   ```
   This will prompt for your Client ID/Secret, open your browser to Yahoo's
   login page, and ask you to paste back the `code=` value from the
   redirected URL (the redirect page itself won't load -- that's expected,
   just copy the code out of the browser's address bar). It then lists your
   MLB fantasy leagues so you can pick which one to track, and automatically
   looks for prior seasons of that same league (Yahoo hides these in its UI,
   but the API still has them).

4. Recover full history for every season it found:
   ```
   python -m app backfill --all
   ```
   This also walks day-by-day through the current season pulling each day's
   individual stat contribution (Yahoo can answer "what happened on July 18"
   for any past day even though it can't answer "what were the cumulative
   totals as of July 18"), so counting-stat trends (HR, RBI, SB, ...) come
   back fully populated for the whole season immediately, not just from
   today forward. This makes one API request per day of the season, so it
   can take a few minutes the first time -- it's resumable, so re-running it
   only fetches whatever's still missing.

5. Start the dashboard (leave this running -- it pulls daily and catches up
   automatically after sleep/reboot):
   ```
   python -m app serve
   ```
   Then open http://localhost:8765

That's it. No further logins are needed unless you revoke the app's access
in your Yahoo account.

## CLI reference

| Command | What it does |
|---|---|
| `python -m app auth` | One-time OAuth setup + league selection |
| `python -m app pull` | Run one manual pull right now |
| `python -m app backfill --all` | Recover every known season's history |
| `python -m app backfill --season 2022` | Recover a single season |
| `python -m app serve [--host] [--port]` | Run the dashboard + daily scheduler |
| `python -m app status` | Show config/auth/database status |
| `python -m app diagnose` | Per-category data coverage + raw standings shape, for debugging gaps |

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

- **`status` shows "Authenticated: False"** -- run `python -m app auth` again.
- **Yahoo access was revoked** -- the daily pull will log an `invalid_grant`
  error and the dashboard header will show a stale-data warning; re-run
  `python -m app auth` to reconnect.
- **A field looks wrong in the dashboard** -- every raw API response is saved
  verbatim in the `raw_responses` table specifically so parsing issues can be
  fixed without re-fetching anything. See `app/yahoo/parse.py`.
