"""NFL team bye weeks (Phase 5 step 5).

There is no "bye week" field anywhere upstream. It's derived: a team's bye is
the one regular-season week its schedule has no game for. nflverse's schedule
data (already a dependency via nflreadpy, same as roster/stat CSVs) is enough
on its own -- checked against 2024/2025/2026, every one of the 32 teams has
exactly one such week every season, so this is a closed calculation, not a
heuristic.

Same shape as `nfl_stats.py`'s position map: fetch once into the gitignored
`data/raw/`, derive, and write a small committed snapshot
(`data/config/nfl_bye_weeks.json`) so a deployed container -- which never has
the raw schedule CSVs -- still has byes without needing them.
"""
import json
from pathlib import Path
from typing import Dict, Optional

import nflreadpy as nfl

from .paths import NFL_BYE_WEEKS_FILE, RAW_NFL_SCHEDULES_DIR, ensure_parent_dir
from .player_registry import normalize_team


def fetch_and_save_schedule(season: int) -> Path:
    """Fetch one season's schedule and save as CSV. Small (~285 rows/season),
    unlike the weekly stats CSVs -- fine to keep every season on disk."""
    df = nfl.load_schedules(seasons=[season])
    output_path = RAW_NFL_SCHEDULES_DIR / f'{season}.csv'
    ensure_parent_dir(output_path)
    df.write_csv(str(output_path))
    return output_path


def _load_schedule_rows(season: int, directory: Path = RAW_NFL_SCHEDULES_DIR) -> list:
    import csv  # pylint: disable=import-outside-toplevel

    path = directory / f'{season}.csv'
    try:
        with open(path, encoding='utf-8') as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def compute_bye_weeks(season: int, directory: Path = RAW_NFL_SCHEDULES_DIR) -> Dict[str, int]:
    """{team_abbr: bye_week} for one season, from the saved schedule CSV.

    Regular season only ('REG') -- a bye is a week with no game scheduled at
    all, and postseason weeks are sparse for every team for an unrelated
    reason (elimination), which would otherwise look like a second bye.

    Team codes are run through player_registry.normalize_team() so the keys
    here always match a resolved player's `.team` -- nflverse's schedule
    spells the Rams 'LA' (checked: it's the only such mismatch that showed up
    against a real roster), and a raw 'LA' key silently misses every LAR
    player looking up their bye through app/repository.py.
    """
    rows = _load_schedule_rows(season, directory)
    if not rows:
        return {}

    teams = set()
    weeks_by_team: Dict[str, set] = {}
    all_weeks = set()
    for row in rows:
        if row.get('game_type') != 'REG':
            continue
        try:
            week = int(row['week'])
        except (KeyError, TypeError, ValueError):
            continue
        all_weeks.add(week)
        for side in ('home_team', 'away_team'):
            team = normalize_team(row.get(side))
            if team:
                teams.add(team)
                weeks_by_team.setdefault(team, set()).add(week)

    byes = {}
    for team in teams:
        missing = sorted(all_weeks - weeks_by_team.get(team, set()))
        # Not asserted to be exactly one: a season saved mid-fetch, or a
        # future season whose schedule isn't fully released yet, can
        # legitimately have zero or several "missing" weeks. Only a clean
        # single answer is trustworthy enough to report.
        if len(missing) == 1:
            byes[team] = missing[0]
    return byes


def _snapshot() -> Dict[str, Dict[str, int]]:
    try:
        return json.loads(NFL_BYE_WEEKS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_bye_weeks_snapshot(seasons: list) -> Dict[int, int]:
    """Write the committed {season_str: {team: week}} snapshot. Returns
    {season: team count} so the CLI can report what it wrote."""
    snapshot: Dict[str, Dict[str, int]] = {}
    for season in seasons:
        byes = compute_bye_weeks(season)
        if byes:
            snapshot[str(season)] = byes
    ensure_parent_dir(NFL_BYE_WEEKS_FILE)
    NFL_BYE_WEEKS_FILE.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    return {int(season): len(teams) for season, teams in snapshot.items()}


def bye_week_map(season: int) -> Dict[str, int]:
    """{team_abbr: bye_week} for a season. Live schedule CSV when the local
    checkout has one, else the committed snapshot -- same fallback order as
    fantasy_position_map(), and for the same reason: the CSVs live under the
    gitignored data/raw/, so a deployed container only ever has the snapshot.
    """
    live = compute_bye_weeks(season)
    if live:
        return live
    return _snapshot().get(str(season), {})


def bye_week_for_team(team: Optional[str], season: int) -> Optional[int]:
    normalized = normalize_team(team)
    if not normalized:
        return None
    return bye_week_map(season).get(normalized)
