# Fantasy Baseball History

Yahoo removed the ability to see your league's history. This app pulls your
team and league results from Yahoo's official Fantasy Sports API once a day,
builds up history over time in a local SQLite database, and serves a local,
interactive dashboard so you can explore and filter every stat it has
collected.

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

## Dashboard

Six tabs, all filterable/sortable, no page reloads:

- **Standings** -- current (or any-date) standings table, plus a rank-over-time
  chart across the season with a clickable legend to isolate teams.
- **Matchups** -- weekly results filterable by week/team; click a row to expand
  the category-by-category breakdown.
- **Head-to-Head** -- all-time win/loss record between every pair of managers
  (tracked by Yahoo manager GUID, so it follows people across seasons/renamed
  teams), with a toggle for a single season vs. combined history.
- **Categories** -- per-team win rate for each stat category, as a bar chart
  plus a full table.
- **Transactions** -- add/drop/trade log, filterable by team, type, and player
  name search.
- **History** -- one row per season showing final standings once Yahoo marks
  the season finished.

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
