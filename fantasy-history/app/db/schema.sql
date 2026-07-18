-- Yahoo Fantasy Baseball history schema.
-- Everything is keyed by season_year so multiple seasons coexist.
-- matchup_team_stats.value and similar stat "value" columns are TEXT
-- because Yahoo returns strings like "3.86" or "12/45"; cast in queries.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS seasons (
    season_year INTEGER PRIMARY KEY,
    game_key TEXT NOT NULL,
    league_key TEXT NOT NULL UNIQUE,
    league_name TEXT,
    num_teams INTEGER,
    scoring_type TEXT,           -- 'head' | 'headpoint' | 'roto' | 'point'
    start_week INTEGER,
    end_week INTEGER,
    start_date TEXT,
    end_date TEXT,
    playoff_start_week INTEGER,
    is_finished INTEGER DEFAULT 0,
    settings_json TEXT
);

CREATE TABLE IF NOT EXISTS stat_categories (
    season_year INTEGER NOT NULL,
    stat_id INTEGER NOT NULL,
    name TEXT,
    display_name TEXT,
    sort_order INTEGER,
    is_display_only INTEGER DEFAULT 0,
    position_type TEXT,
    PRIMARY KEY (season_year, stat_id)
);

CREATE TABLE IF NOT EXISTS teams (
    season_year INTEGER NOT NULL,
    team_key TEXT PRIMARY KEY,
    team_id TEXT,
    name TEXT,
    logo_url TEXT,
    manager_nickname TEXT,
    manager_guid TEXT,
    division_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_teams_season ON teams(season_year);
CREATE INDEX IF NOT EXISTS idx_teams_manager_guid ON teams(manager_guid);

CREATE TABLE IF NOT EXISTS matchups (
    matchup_id TEXT PRIMARY KEY,   -- synthetic: f"{season_year}:{week}:{team1_key}:{team2_key}"
    season_year INTEGER NOT NULL,
    week INTEGER NOT NULL,
    team1_key TEXT NOT NULL,
    team2_key TEXT NOT NULL,
    is_playoffs INTEGER DEFAULT 0,
    is_consolation INTEGER DEFAULT 0,
    status TEXT,                   -- 'preevent' | 'midevent' | 'postevent'
    winner_team_key TEXT,
    is_tied INTEGER DEFAULT 0,
    week_start_date TEXT,
    week_end_date TEXT
);
CREATE INDEX IF NOT EXISTS idx_matchups_season_week ON matchups(season_year, week);
CREATE INDEX IF NOT EXISTS idx_matchups_teams ON matchups(team1_key, team2_key);

CREATE TABLE IF NOT EXISTS matchup_team_stats (
    matchup_id TEXT NOT NULL,
    team_key TEXT NOT NULL,
    stat_id INTEGER NOT NULL,
    value TEXT,
    won_category INTEGER,
    tied_category INTEGER,
    PRIMARY KEY (matchup_id, team_key, stat_id)
);

CREATE TABLE IF NOT EXISTS standings_snapshots (
    snapshot_date TEXT NOT NULL,   -- ISO date of the pull
    season_year INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    rank INTEGER,
    wins INTEGER,
    losses INTEGER,
    ties INTEGER,
    pct REAL,
    games_back TEXT,
    points_for REAL,
    points_against REAL,
    playoff_seed INTEGER,
    PRIMARY KEY (snapshot_date, team_key)
);
CREATE INDEX IF NOT EXISTS idx_standings_season ON standings_snapshots(season_year);

-- One row per (day, team, stat) so category totals (HR, RBI, ERA, ...)
-- build up daily history within a season, not just a "latest" snapshot --
-- this is what lets the dashboard show day-to-day and cumulative trends
-- for every stat, not just win/loss rank.
CREATE TABLE IF NOT EXISTS team_stat_snapshots (
    snapshot_date TEXT NOT NULL,
    season_year INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    stat_id INTEGER NOT NULL,
    value TEXT,
    PRIMARY KEY (snapshot_date, team_key, stat_id)
);
CREATE INDEX IF NOT EXISTS idx_team_stat_snapshots_season ON team_stat_snapshots(season_year, stat_id);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_key TEXT PRIMARY KEY,
    season_year INTEGER NOT NULL,
    type TEXT,                     -- 'add' | 'drop' | 'add/drop' | 'trade'
    status TEXT,
    timestamp INTEGER,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_transactions_season ON transactions(season_year);

CREATE TABLE IF NOT EXISTS transaction_players (
    transaction_key TEXT NOT NULL,
    player_key TEXT NOT NULL,
    player_name TEXT,
    movement TEXT,                 -- 'add' | 'drop' | 'traded'
    source_team_key TEXT,
    dest_team_key TEXT,
    PRIMARY KEY (transaction_key, player_key, movement)
);

CREATE TABLE IF NOT EXISTS final_standings (
    season_year INTEGER NOT NULL,
    team_key TEXT NOT NULL,
    final_rank INTEGER,
    PRIMARY KEY (season_year, team_key)
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started_at TEXT NOT NULL,
    run_finished_at TEXT,
    kind TEXT NOT NULL,             -- 'daily' | 'backfill' | 'manual'
    status TEXT NOT NULL,           -- 'ok' | 'partial' | 'error'
    detail TEXT
);

CREATE TABLE IF NOT EXISTS raw_responses (
    endpoint TEXT NOT NULL,
    params TEXT NOT NULL,
    season_year INTEGER,
    week INTEGER,
    fetched_at TEXT NOT NULL,
    body_json TEXT NOT NULL,
    PRIMARY KEY (endpoint, params)
);
