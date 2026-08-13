import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import nflreadpy as nfl

from .paths import (
    RAW_NFL_WEEKLY_STATS_DIR,
    RAW_NFL_SEASONAL_STATS_DIR,
    RAW_NFL_ROSTERS_DIR,
    NFL_POSITION_MAP_FILE,
    ensure_parent_dir,
)

FIRST_SEASON = 2019


def current_nfl_season() -> int:
    """Return current NFL season year.
    NFL season label matches the year it starts in; season is "current"
    from start (Sept) through following February. We count month >= 3 as
    being in the current calendar year's season."""
    now = datetime.now()
    return now.year if now.month >= 3 else now.year - 1


def fetch_and_save_weekly_stats(seasons: List[int]) -> Dict[int, Path]:
    """Fetch weekly player stats for given seasons and save as CSVs.
    Returns: {season: output_path}"""
    result = {}
    for season in seasons:
        print(f'  Fetching weekly stats for {season}...')
        try:
            df = nfl.load_player_stats(seasons=season, summary_level='week')
            output_path = RAW_NFL_WEEKLY_STATS_DIR / f'{season}.csv'
            ensure_parent_dir(output_path)
            df.write_csv(str(output_path))
            result[season] = output_path
            print(f'    Wrote {len(df)} rows')
        except Exception as e:
            print(f'    Error: {e}')
    return result


def fetch_and_save_seasonal_stats(seasons: List[int]) -> Dict[int, Path]:
    """Fetch seasonal player stats for given seasons and save as CSVs.
    Returns: {season: output_path}"""
    result = {}
    for season in seasons:
        print(f'  Fetching seasonal stats for {season}...')
        try:
            df = nfl.load_player_stats(seasons=season, summary_level='reg')
            output_path = RAW_NFL_SEASONAL_STATS_DIR / f'{season}.csv'
            ensure_parent_dir(output_path)
            df.write_csv(str(output_path))
            result[season] = output_path
            print(f'    Wrote {len(df)} rows')
        except Exception as e:
            print(f'    Error: {e}')
    return result


def fetch_and_save_rosters(seasons: List[int]) -> Dict[int, Path]:
    """Fetch rosters for given seasons and save as CSVs.
    Returns: {season: output_path}"""
    result = {}
    for season in seasons:
        print(f'  Fetching rosters for {season}...')
        try:
            df = nfl.load_rosters(seasons=season)
            output_path = RAW_NFL_ROSTERS_DIR / f'{season}.csv'
            ensure_parent_dir(output_path)
            df.write_csv(str(output_path))
            result[season] = output_path
            print(f'    Wrote {len(df)} rows')
        except Exception as e:
            print(f'    Error: {e}')
    return result


def refresh_nfl_stats(seasons: Optional[List[int]] = None) -> Dict[str, Dict[int, Path]]:
    """Refresh NFL stats for given seasons.
    seasons=None means FIRST_SEASON..current_season (inclusive).
    Returns: {datatype: {season: path}}"""
    if seasons is None:
        now = datetime.now()
        end_season = now.year if now.month >= 3 else now.year - 1
        seasons = list(range(FIRST_SEASON, end_season + 1))

    return {
        'weekly': fetch_and_save_weekly_stats(seasons),
        'seasonal': fetch_and_save_seasonal_stats(seasons),
        'rosters': fetch_and_save_rosters(seasons),
    }


def load_weekly_stats(season: int, directory: Path = RAW_NFL_WEEKLY_STATS_DIR) -> Optional[List[Dict[str, Any]]]:
    """Load weekly stats for a season from CSV.
    Returns: list of dicts or None if file missing."""
    path = directory / f'{season}.csv'
    if not path.exists():
        return None

    result = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            result.append(row)
    return result


def load_seasonal_stats(season: int, directory: Path = RAW_NFL_SEASONAL_STATS_DIR) -> Optional[List[Dict[str, Any]]]:
    """Load seasonal stats for a season from CSV.
    Returns: list of dicts or None if file missing."""
    path = directory / f'{season}.csv'
    if not path.exists():
        return None

    result = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            result.append(row)
    return result


# Positions a fantasy roster can actually hold. Used to break name collisions
# between a fantasy-relevant player and a defensive namesake.
FANTASY_POSITIONS = ('QB', 'RB', 'WR', 'TE', 'K')


def _build_position_map(rosters: List[Dict[str, Any]]) -> Dict[str, str]:
    """Shared by the live CSV path and the committed snapshot, so the two can
    never disagree about how a name collision resolves."""
    position_map: Dict[str, str] = {}
    for row in rosters:
        name = str(row.get('full_name', '')).strip().lower()
        position = str(row.get('position', '') or '').strip().upper()
        if not name or not position:
            continue
        existing = position_map.get(name)
        # First writer wins unless the incumbent is a non-fantasy position and
        # the challenger is a fantasy one.
        if existing is None or (existing not in FANTASY_POSITIONS and position in FANTASY_POSITIONS):
            position_map[name] = position
    return position_map


def _position_map_snapshot() -> Dict[str, Dict[str, str]]:
    """{season_str: {name: position}} from the committed snapshot, or {}."""
    try:
        return json.loads(NFL_POSITION_MAP_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_position_map_snapshot(seasons: List[int]) -> Dict[str, int]:
    """Write the committed name->position snapshot from the local roster CSVs.

    Deliberately keeps every position, not just the fantasy ones: the
    fantasy-vs-defender tiebreak in fantasy_position_map() needs to know a
    namesake exists, and dropping the defenders would reintroduce exactly the
    collision bug that function documents. Returns {season: name count}.
    """
    snapshot: Dict[str, Dict[str, str]] = {}
    for season in seasons:
        rows = load_rosters(season) or []
        if not rows:
            continue
        snapshot[str(season)] = _build_position_map(rows)
    ensure_parent_dir(NFL_POSITION_MAP_FILE)
    NFL_POSITION_MAP_FILE.write_text(json.dumps(snapshot, separators=(',', ':'), sort_keys=True))
    return {season: len(names) for season, names in snapshot.items()}


def fantasy_position_map(season: int, directory: Path = RAW_NFL_ROSTERS_DIR) -> Dict[str, str]:
    """{lowercased_player_name: position} for a season, preferring fantasy-relevant
    positions when a name is shared.

    Build name->position maps with THIS, not a plain dict comprehension over
    load_rosters(). Several fantasy stars share a name with a defensive player
    -- Josh Allen (BUF QB / JAX LB), Lamar Jackson (BAL QB / CAR-ATL DB) -- and
    a naive comprehension keeps whichever row happens to come last. That
    silently mislabeled this league's round-1 rushing QBs as defenders, which
    dropped them from the QB draft-slot targets and put phantom 'DB'/'LB' rows
    in the round-1 draft analysis (found and fixed 2026-08-11).

    Team defenses are NOT in nflverse rosters at all (they're individual-player
    records), so DST never resolves through here -- that's a data limit, not a
    bug to chase.

    Falls back to the committed snapshot (data/config/nfl_position_map.json)
    when the season's CSV is absent. The CSVs live under the gitignored
    data/raw/, so a deployed container has none of them, and an empty map
    degrades *silently* -- the draft-patterns page renders "no draft history"
    and the QB historical adjustment quietly finds no targets. The snapshot
    is ~100KB and regenerated by `python3 -m app snapshot-position-map`.
    """
    rosters = load_rosters(season, directory) or []
    if not rosters:
        return _position_map_snapshot().get(str(season), {})
    return _build_position_map(rosters)


def load_qb_rushing_yards(season: int, directory: Path = RAW_NFL_SEASONAL_STATS_DIR) -> Dict[str, int]:
    """QB rushing yards for a season as {lowercased_player_name: rushing_yards}.
    Falls back to the previous season when the requested one isn't saved yet
    (back to 2020); {} if nothing is available.

    This is the only rushing-production lookup in the codebase, and it's here
    because this league pays a real round-1 premium for RUSHING quarterbacks
    and treats pocket passers as replaceable -- see keeperRules.behavioralNotes
    in data/config/league_rules.json, which currently calls that a manual
    judgment override for want of exactly this data. Kept when the
    ranking_adjustments module it used to live in was deleted (2026-08-11),
    so automating that premium doesn't start from scratch."""
    stats = load_seasonal_stats(season, directory)
    if not stats:
        return load_qb_rushing_yards(season - 1, directory) if season > 2020 else {}

    qb_rushing: Dict[str, int] = {}
    for row in stats:
        if row.get('position') != 'QB':
            continue
        name = str(row.get('player_display_name') or row.get('player_name') or '').strip().lower()
        rushing_yards = row.get('rushing_yards')
        if not name or rushing_yards is None:
            continue
        try:
            qb_rushing[name] = int(float(rushing_yards))
        except (ValueError, TypeError):
            continue
    return qb_rushing


def load_rosters(season: int, directory: Path = RAW_NFL_ROSTERS_DIR) -> Optional[List[Dict[str, Any]]]:
    """Load rosters for a season from CSV.
    Returns: list of dicts or None if file missing."""
    path = directory / f'{season}.csv'
    if not path.exists():
        return None

    result = []
    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            result.append(row)
    return result
