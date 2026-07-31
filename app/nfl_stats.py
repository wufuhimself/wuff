import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import nflreadpy as nfl

from .paths import (
    RAW_NFL_WEEKLY_STATS_DIR,
    RAW_NFL_SEASONAL_STATS_DIR,
    RAW_NFL_ROSTERS_DIR,
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
