"""Parses Yahoo Fantasy's own website HTML (not the API -- see the
package docstring in app/scrape/__init__.py for why).

VALIDATED against 5 real saved Yahoo pages (View Page Source exports of
this league's actual standings/draft-results/transactions/league-home/
team pages) during development of this module -- see
app/tests/fixtures/scrape/ for hand-crafted minimal fixtures that mirror
the confirmed real structure, and the docstring on each function below for
exactly what was and wasn't checked against the real files.

IMPORTANT quirk confirmed against the real saved pages: a browser
extension injects markup (classes like `fp-player-match`,
`greaseBallCenter`, `fpIcon`) INSIDE Yahoo's own player-name links. It
doesn't change the visible text, but it does break naive
`element.get_text(strip=True)` -- bs4's default separator="" independently
strips each text fragment's own leading/trailing whitespace BEFORE joining
them with nothing, silently swallowing inter-tag spaces (e.g. "Jung Hoo
Lee" -> "JungHooLee", "Shohei Ohtani (Batter) (LAD - Util)" -> "Shohei
Ohtani(Batter)(LAD - Util)"). Every text extraction in this module uses
`element.get_text(" ", strip=True)` (explicit single-space separator) for
exactly this reason -- never use the bare/default form here.

Also confirmed: long team/player display names get truncated with "..."
in the visible text, but the full name survives in a `title` attribute on
the containing element, so `.get("title")` is preferred over
`.get_text(...)` wherever a title attribute is present.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

TEAM_LINK_RE = re.compile(r"/b1/(\d+)/(\d+)$")
PLAYER_LINK_RE = re.compile(r"/mlb/players/(\d+)$")
PLAYER_NAME_RE = re.compile(
    r"^(?P<name>.+?)(?: \(Batter\))? \((?P<mlb_team>[A-Za-z.]+) - (?P<pos>[A-Za-z0-9,]+)\)$"
)
COST_PREFIX_RE = re.compile(r"^\$(\d+)\s+(.*)$")
_TRAILING_DUP_SUFFIX_RE = re.compile(r"_\d+$")

# Group-header labels on the standings page (row 0 of each table) that
# mark which half of the table a stat column belongs to -- confirmed
# against the real standings page (colspan=2 for Rank+Team Name, then a
# colspan-N "Batting" group, then a colspan-N "Pitching" group; the
# "points" table additionally has 2 trailing ungrouped columns, "Total
# Points" and "Pts Change", after the Pitching group).
_GROUP_LABEL_TO_POSITION_TYPE = {"Batting": "B", "Pitching": "P"}


def _team_id_from_href(href: str | None) -> tuple[str, str] | None:
    """Returns (league_id, team_id) parsed out of a team-page-style href
    (`.../b1/{league_id}/{team_id}`), or None if href doesn't match."""
    if not href:
        return None
    m = TEAM_LINK_RE.search(href)
    if not m:
        return None
    return m.group(1), m.group(2)


def normalize_stat_column(col_key: str) -> tuple[str, bool]:
    """"K_2" (the bs4-side disambiguation suffix this module adds for a
    second column literally named "K") -> ("K", False). "GP *" (Yahoo's
    own display-only marker, confirmed present on the real standings
    "Overall Stats" table for GP and IP) -> ("GP", True)."""
    name = _TRAILING_DUP_SUFFIX_RE.sub("", col_key).strip()
    is_display_only = name.endswith("*")
    if is_display_only:
        name = name[:-1].strip()
    return name, is_display_only


def _stat_position_types(rows: list, col_keys: list[str]) -> dict[str, str | None]:
    """Maps each column key to "B"/"P"/None using the group-header row's
    colspans (see _GROUP_LABEL_TO_POSITION_TYPE above)."""
    group_cells = rows[0].find_all(["th", "td"])
    types: dict[str, str | None] = {}
    idx = 0
    for cell in group_cells:
        span = int(cell.get("colspan") or 1)
        label = cell.get_text(" ", strip=True)
        ptype = _GROUP_LABEL_TO_POSITION_TYPE.get(label)
        for i in range(idx, idx + span):
            if i < len(col_keys):
                types[col_keys[i]] = ptype
        idx += span
    return types


# ---------------------------------------------------------------------
# Standings (/standings)
# ---------------------------------------------------------------------

def parse_standings_tables(html: str) -> dict[str, Any]:
    """VALIDATED against the real saved standings page: returns 12
    "points" rows and 12 "stats" rows with correct values (e.g. Rank 1 =
    "Prime Time", league_id="74647", team_id="9").

    Returns {"points": [...], "stats": [...], "column_position_types":
    {"points": {col_key: "B"|"P"|None}, "stats": {...}}}.

    The two tables are identified by the heading text ("Overall Points" /
    "Overall Stats") immediately preceding them, NOT by table id/class --
    Yahoo's table ids are randomly generated per page load (confirmed
    empirically, so hardcoding one would silently break on the next
    scrape). The "points" table's team-name cell has an
    <a href="https://baseball.fantasysports.yahoo.com/b1/{league_id}/{team_id}">
    link (use it for team identity); the "stats" table's team-name cell
    has NO link (confirmed) -- its rows must be matched to the "points"
    table's rows by team name string within the same page/pull to recover
    league_id/team_id (see app.scrape.jobs.ingest_standings_html).
    """
    soup = BeautifulSoup(html, "lxml")
    result: dict[str, Any] = {
        "points": [],
        "stats": [],
        "column_position_types": {"points": {}, "stats": {}},
    }
    for table in soup.find_all("table"):
        heading = None
        node = table
        for _ in range(8):
            node = node.find_previous(string=True)
            if node and node.strip():
                heading = node.strip()
                break
        kind = {"Overall Points": "points", "Overall Stats": "stats"}.get(heading)
        if kind is None:
            continue
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[1].find_all(["th", "td"])
        headers = [c.get_text(" ", strip=True) for c in header_cells]
        seen: dict[str, int] = {}
        col_keys = []
        for h in headers:
            seen[h] = seen.get(h, 0) + 1
            col_keys.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
        result["column_position_types"][kind] = _stat_position_types(rows, col_keys)
        for r in rows[2:]:
            cells = r.find_all(["th", "td"])
            if len(cells) != len(col_keys):
                continue
            team_link = cells[1].find("a")
            ids = _team_id_from_href(team_link.get("href") if team_link else None)
            row: dict[str, Any] = {"league_id": ids[0] if ids else None, "team_id": ids[1] if ids else None}
            for key, cell in zip(col_keys, cells):
                if key == "Team Name":
                    row[key] = cell.get("title") or cell.get_text(" ", strip=True)
                else:
                    row[key] = cell.get_text(" ", strip=True)
            result[kind].append(row)
    return result


# ---------------------------------------------------------------------
# Draft results (/draftresults)
# ---------------------------------------------------------------------

def parse_draft_results(html: str) -> list[dict[str, Any]]:
    """VALIDATED against the real saved draft-results page: returns 348
    correct picks across 29 rounds (round 1 pick 1 = Aaron Judge,
    player_yahoo_id="9877", team_name="Backcrackers"; round 1 pick 2 =
    "Shohei Ohtani" with mlb_team="LAD", position="Util",
    team_name="Team Grimace (Gri-MAH-Chay)" -- the full untruncated name,
    recovered via the cell's `title` attribute).

    One <table> per round: the first row is a single-cell "Round N"
    marker; each subsequent row is [pick_number, player cell with a link
    to sports.yahoo.com/mlb/players/{id} and text "Name (TEAM - POS)" (or
    "Name (Batter) (TEAM - POS)" for two-way players), team name cell
    (plain text, no id -- match by name against that season's standings
    page teams). No $cost column observed in this league (snake draft);
    if one shows up for an auction league elsewhere, it's expected to be
    a 4th cell -- handled defensively below but UNVERIFIED, since no
    auction-league sample was available.
    """
    soup = BeautifulSoup(html, "lxml")
    picks = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        first_cells = rows[0].find_all(["th", "td"])
        first_text = first_cells[0].get_text(" ", strip=True) if first_cells else ""
        m = re.match(r"Round (\d+)", first_text)
        if not m:
            continue
        round_num = int(m.group(1))
        for r in rows[1:]:
            cells = r.find_all(["th", "td"])
            if len(cells) < 3:
                continue
            pick_text = cells[0].get_text(" ", strip=True).rstrip(".")
            player_cell = cells[1]
            team_name = cells[2].get("title") or cells[2].get_text(" ", strip=True)
            player_link = player_cell.find("a")
            player_yahoo_id = None
            if player_link:
                pm = PLAYER_LINK_RE.search(player_link.get("href") or "")
                if pm:
                    player_yahoo_id = pm.group(1)
            player_raw = player_cell.get_text(" ", strip=True)
            name_m = PLAYER_NAME_RE.match(player_raw)
            cost = None
            if len(cells) >= 4:
                # UNVERIFIED: no auction-league sample was available to
                # confirm this shape; defensive best-effort only.
                cost_text = cells[3].get_text(" ", strip=True).lstrip("$")
                cost = int(cost_text) if cost_text.isdigit() else None
            picks.append(
                {
                    "round": round_num,
                    "pick_in_round": int(pick_text) if pick_text.isdigit() else None,
                    "player_yahoo_id": player_yahoo_id,
                    "player_name": name_m.group("name") if name_m else player_raw,
                    "mlb_team": name_m.group("mlb_team") if name_m else None,
                    "position": name_m.group("pos") if name_m else None,
                    "team_name": team_name,
                    "cost": cost,
                }
            )
    return picks


# ---------------------------------------------------------------------
# Transactions (/transactions)
# ---------------------------------------------------------------------

def _classify_movement(raw_text: str) -> tuple[str, int | None]:
    """Maps a scraped <h6> movement string to (movement, cost), where
    movement is one of 'add' / 'drop' / 'traded' -- matching the existing
    transaction_players.movement vocabulary already in schema.sql.

    Confirmed against the real saved transactions page: "Waiver" and "$N
    Waiver" (added off waivers, optionally with a FAAB cost) and "To
    Waivers" (dropped to waivers). "Free Agent" / "To Free Agents" /
    "Trade" are NOT present in that page (this league's recent activity
    happened to be all-waiver) -- handled here defensively by the same
    phrasing pattern ("To ..." => drop, exact "Trade" => traded, else
    => add) but UNVERIFIED against a real trade or free-agent-pickup row.
    """
    text = raw_text.strip()
    cost = None
    m = COST_PREFIX_RE.match(text)
    if m:
        cost = int(m.group(1))
        text = m.group(2).strip()
    lowered = text.lower()
    if lowered == "trade":
        return "traded", cost
    if lowered.startswith("to "):
        return "drop", cost
    return "add", cost


def parse_transactions(html: str) -> list[dict[str, Any]]:
    """VALIDATED against the real saved transactions page: returns all 25
    rows on that page correctly, including the "Jung Hoo Lee" row where
    the injected extension markup splits the name across three text
    fragments ("Jung" / "Hoo" / "Lee") -- get_text(" ", strip=True) on the
    player <a> recombines it correctly -- and the two rows that have only
    one player div (an add with no corresponding drop, e.g. filling an
    open roster spot).

    Identified via the `Tst-transaction-table` class (stable, unlike the
    standings page's random-id tables). Each data <tr> has 3 <td>s:
    icon-only (skipped), a player cell (colspan=2) containing one
    `<div class="Pbot-xs">` per player moved (usually 2: one added, one
    dropped), and a team cell with `<a class="Tst-team-name"
    href=".../b1/{league_id}/{team_id}">{team name}</a>` plus
    `<span class="F-timestamp">{human date, e.g. "Aug 7, 11:41 am", no
    year}</span>`.

    Returns one dict per <tr> (== one Yahoo transaction -- Yahoo groups a
    waiver claim's add+drop into a single row): {"league_id", "team_id",
    "team_name", "timestamp_text", "players": [{"player_yahoo_id",
    "player_name", "mlb_team", "position", "movement", "cost"}, ...]}.
    No transaction_key/type is produced here -- see
    app.scrape.jobs.scrape_pull_transactions for how those are derived/
    synthesized from this shape.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find(class_="Tst-transaction-table")
    if table is None:
        return []
    results = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        player_cell = cells[-2]
        team_cell = cells[-1]
        team_link = team_cell.find("a", class_="Tst-team-name")
        if team_link is None:
            continue
        ids = _team_id_from_href(team_link.get("href"))
        timestamp_el = team_cell.find("span", class_="F-timestamp")
        players = []
        for div in player_cell.find_all("div", class_="Pbot-xs"):
            player_link = div.find("a", href=PLAYER_LINK_RE)
            if player_link is None:
                continue
            pm = PLAYER_LINK_RE.search(player_link.get("href") or "")
            position_el = div.find("span", class_="F-position")
            movement_el = div.find("h6")
            mlb_team = position = None
            if position_el is not None:
                pos_text = position_el.get_text(" ", strip=True)
                if " - " in pos_text:
                    mlb_team, position = pos_text.split(" - ", 1)
            if movement_el is not None:
                movement, cost = _classify_movement(movement_el.get_text(" ", strip=True))
            else:
                movement, cost = "add", None
            players.append(
                {
                    "player_yahoo_id": pm.group(1) if pm else None,
                    "player_name": player_link.get_text(" ", strip=True),
                    "mlb_team": mlb_team,
                    "position": position,
                    "movement": movement,
                    "cost": cost,
                }
            )
        if not players:
            continue
        results.append(
            {
                "league_id": ids[0] if ids else None,
                "team_id": ids[1] if ids else None,
                "team_name": team_link.get("title") or team_link.get_text(" ", strip=True),
                "timestamp_text": timestamp_el.get_text(" ", strip=True) if timestamp_el else None,
                "players": players,
            }
        )
    return results


# ---------------------------------------------------------------------
# Team roster page (team page's statTable0/statTable1)
# ---------------------------------------------------------------------

def parse_team_roster(html: str) -> dict[str, list[dict[str, Any]]]:
    """TODO -- NOT IMPLEMENTED. Lower priority per the scraping-pivot
    task; stubbed here with the confirmed structure so a future session
    doesn't need to re-derive it from a saved page.

    Confirmed against the real saved team page (unlike the standings
    page, these ids are STABLE across loads):
      - `#statTable0` = batters. Row 0 (group headers): ['', '', '',
        'Rank', 'Fantasy', 'Batting', '']. Row 1 (column headers): ['Pos',
        'Batters', 'Opp', 'Pre-Season', '% Start', '% Ros', 'H/AB*', 'R',
        'HR', 'RBI', 'SB', 'K', 'OBP', ''].
      - `#statTable1` = pitchers. Row 0: ['', '', '', 'Rank', 'Fantasy',
        'Pitching', '']. Row 1: ['Pos', 'Pitchers', 'Opp', 'Pre-Season',
        '% Start', '% Ros', 'IP*', 'W', 'SV', 'K', 'HLD', 'ERA', 'WHIP',
        ''].
      - Player name/id is expected in the "Batters"/"Pitchers" column via
        the same `PLAYER_LINK_RE`-shaped <a> as draft results/
        transactions, but the exact cell markup for that column was NOT
        inspected in detail (only the header rows were confirmed) -- do
        that before writing the real parser body.
    """
    raise NotImplementedError(
        "parse_team_roster is stubbed -- see this function's docstring for the "
        "confirmed #statTable0/#statTable1 header structure to build it from."
    )
