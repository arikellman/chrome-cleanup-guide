"""Rotisserie scoring math.

Yahoo's roto "Overall Points" table isn't a field the API hands back --
it's Yahoo's own website computing, per category, each team's rank among
the league and converting that rank to points (best team in a category
gets N points in an N-team league, worst gets 1; tied teams split the
points they'd occupy). This module reproduces that computation from the
raw per-category stat totals we already pull daily (team_stat_snapshots),
using each category's `sort_order` (1 = higher value wins the category,
0 = lower value wins, e.g. ERA/WHIP) to know which direction is "better".

Caveat: Yahoo applies an additional innings-pitched/at-bat qualifier
threshold to rate stats (ERA, WHIP, OBP, ...) that penalizes teams below
a minimum workload -- that threshold isn't modeled here since it isn't
exposed anywhere in the stat_categories settings we pull. Counting stats
(HR, RBI, SB, W, SV, ...) are unaffected and match Yahoo's own points
exactly; rate-stat points may differ slightly from Yahoo's official
numbers for teams near that workload minimum.
"""
from __future__ import annotations

from typing import Any


def compute_points(values: dict[str, float], higher_is_better: bool) -> dict[str, float]:
    """values: {team_key: numeric stat value} for one category.

    Returns {team_key: points}, where the best team gets len(values) points
    and the worst gets 1, with ties splitting the average of the points
    the tied positions would occupy.
    """
    n = len(values)
    if n == 0:
        return {}

    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=higher_is_better)
    points: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        # Positions i..j (0-indexed, 0 = best) are tied. The position at
        # index p is worth (n - p) points; average those for the group.
        group_points = [n - p for p in range(i, j + 1)]
        avg = sum(group_points) / len(group_points)
        for k in range(i, j + 1):
            points[ordered[k][0]] = avg
        i = j + 1
    return points


def compute_standings(
    team_values: dict[str, dict[int, float]], categories: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """team_values: {team_key: {stat_id: value}}. categories: rows with at
    least "stat_id" and "sort_order" (already filtered to scored, i.e.
    non-display-only, categories by the caller).

    Returns {team_key: {"category_points": {stat_id: points}, "total_points": float}}.
    """
    result: dict[str, dict[str, Any]] = {
        team_key: {"category_points": {}, "total_points": 0.0} for team_key in team_values
    }
    for cat in categories:
        stat_id = cat["stat_id"]
        values = {tk: v[stat_id] for tk, v in team_values.items() if stat_id in v}
        higher_is_better = bool(cat.get("sort_order", 1))
        pts = compute_points(values, higher_is_better)
        for team_key, p in pts.items():
            result[team_key]["category_points"][stat_id] = p
            result[team_key]["total_points"] += p
    return result


def rank_by_total_points(standings: list[dict[str, Any]]) -> None:
    """Assigns standard competition ranking (1, 2, 2, 4, ...) in place,
    mutating each dict's "rank" key. `standings` must already be sorted
    by total_points descending."""
    rank = 0
    prev_points = None
    for idx, row in enumerate(standings, start=1):
        if row["total_points"] != prev_points:
            rank = idx
        row["rank"] = rank
        prev_points = row["total_points"]
