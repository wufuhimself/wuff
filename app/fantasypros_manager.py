"""FantasyPros API client for rankings and projections.

Fetches consensus rankings and player projections from FantasyPros API.
Requires PAID API access (free tier does not include API access, returns 403).

NOTE: If you have free FantasyPros access, use the CSV import workflow instead:
  1. Download rankings CSV from fantasypros.com (copy-paste to CSV file)
  2. Save to data/raw/rankings/fantasypros_rankings.csv
  3. Run: python -m app combine-rankings

API Workflow (PAID TIER ONLY):
1. Add API key to .env: FANTASYPROS_API_KEY=your_key
2. Fetch rankings: python -m app fantasypros-rankings {season}
3. Fetch projections: python -m app fantasypros-projections {season} {week}
4. Combine with other sources: python -m app combine-rankings
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import requests

from .paths import RAW_RANKINGS_DIR, ensure_parent_dir

load_dotenv(dotenv_path=Path('.') / '.env')


FANTASYPROS_API_BASE = "https://api.fantasypros.com/public/v2"


def get_api_key() -> str:
    """Get FantasyPros API key from .env or environment."""
    key = os.getenv("FANTASYPROS_API_KEY")
    if not key:
        raise ValueError(
            "FantasyPros API key not set. "
            "Add to .env file: FANTASYPROS_API_KEY=your_key"
        )
    return key


def fetch_rankings(
    sport: str,
    season: int,
    week: int = 0,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch consensus rankings from FantasyPros API.

    Args:
        sport: NFL, MLB, NBA, NHL, PGA, NCAAF
        season: YYYY (2012+)
        week: 0 (preseason), or specific week (NFL only)
        api_key: FantasyPros API key (default: from env var)

    Returns:
        List of player rankings with structure:
        {fpid, mflid, name, position_id, team_id, rank, ecr, ...}
    """
    if api_key is None:
        api_key = get_api_key()

    url = f"{FANTASYPROS_API_BASE}/{sport}/{season}/rankings"
    headers = {"x-api-key": api_key}
    params = {"week": week}

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    data = response.json()
    players = data.get("players", [])

    return players


def fetch_projections(
    sport: str,
    season: int,
    position: str,
    week: Optional[int] = None,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch player projections from FantasyPros API.

    Args:
        sport: NFL only for now
        season: YYYY
        position: QB, RB, WR, TE, K, DEF, etc
        week: specific week or None (preseason is week=0)
        api_key: FantasyPros API key (default: from env var)

    Returns:
        List of player projections with stats.
    """
    if api_key is None:
        api_key = get_api_key()

    url = f"{FANTASYPROS_API_BASE}/nfl/{season}/projections"
    headers = {"x-api-key": api_key}
    params = {"position": position}
    if week is not None:
        params["week"] = week

    response = requests.get(url, headers=headers, params=params)
    response.raise_for_status()

    data = response.json()
    players = data.get("players", [])

    return players


def normalize_rankings_to_combined_format(
    players: List[Dict[str, Any]],
    source: str = "fantasypros",
) -> List[Dict[str, Any]]:
    """Convert FantasyPros rankings to combined rankings format.

    Standardizes to:
    {
        playerId, playerName, position, team,
        ranking, sourceRanks: {fantasypros: rank}, source
    }
    """
    normalized = []

    for player in players:
        rank_data = player.get("rank", {})
        ecr = rank_data.get("ecr")  # expert consensus rank

        if ecr is None:
            continue  # Skip unranked players

        entry = {
            "playerId": str(player.get("fpid", "")),
            "playerName": player.get("name", "").strip(),
            "position": player.get("position_id", "UNK").upper(),
            "team": player.get("team_id", "UNK").upper(),
            "ranking": int(ecr),
            "source": source,
            "sourceRanks": {source: int(ecr)},
        }

        # Add optional fields if present
        if "mflid" in player:
            entry["mflId"] = player["mflid"]

        normalized.append(entry)

    normalized.sort(key=lambda x: x["ranking"])
    return normalized


def save_rankings(
    rankings: List[Dict[str, Any]],
    sport: str,
    season: int,
    output_dir: Optional[Path] = None,
) -> Path:
    """Save FantasyPros rankings to JSON file."""
    if output_dir is None:
        output_dir = RAW_RANKINGS_DIR

    ensure_parent_dir(output_dir)

    output_path = output_dir / f"fantasypros_{sport.lower()}_{season}_rankings.json"
    with open(output_path, "w") as f:
        json.dump(rankings, f, indent=2)

    return output_path


def save_projections(
    projections: List[Dict[str, Any]],
    sport: str,
    season: int,
    week: Optional[int] = None,
    output_dir: Optional[Path] = None,
) -> Path:
    """Save FantasyPros projections to JSON file."""
    if output_dir is None:
        output_dir = RAW_RANKINGS_DIR

    ensure_parent_dir(output_dir)

    week_str = f"_w{week}" if week is not None else "_preseason"
    output_path = output_dir / f"fantasypros_{sport.lower()}_{season}_projections{week_str}.json"
    with open(output_path, "w") as f:
        json.dump(projections, f, indent=2)

    return output_path


def fetch_and_save_rankings(
    sport: str,
    season: int,
    week: int = 0,
    api_key: Optional[str] = None,
) -> Path:
    """End-to-end: fetch rankings → normalize → save."""
    print(f"Fetching FantasyPros {sport} rankings for {season}...")

    try:
        raw = fetch_rankings(sport, season, week, api_key)
        print(f"  Fetched {len(raw)} total players")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            raise ValueError(
                "API key invalid or expired. Check FANTASYPROS_API_KEY env var."
            )
        raise

    print("Normalizing to combined format...")
    normalized = normalize_rankings_to_combined_format(raw, source="fantasypros")
    print(f"  Normalized {len(normalized)} players with rankings")

    print("Saving...")
    output_path = save_rankings(normalized, sport, season)
    print(f"  Wrote {output_path}")

    return output_path


def fetch_and_save_projections(
    sport: str,
    season: int,
    positions: List[str],
    week: Optional[int] = None,
    api_key: Optional[str] = None,
) -> Path:
    """End-to-end: fetch projections for all positions → save."""
    print(f"Fetching FantasyPros {sport} projections for {season}...")

    all_players = []
    try:
        for pos in positions:
            print(f"  Fetching {pos}...")
            players = fetch_projections(sport, season, pos, week, api_key)
            all_players.extend(players)
            print(f"    Got {len(players)} players")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            raise ValueError(
                "API key invalid or expired. Check FANTASYPROS_API_KEY env var."
            )
        raise

    print(f"Total: {len(all_players)} players")
    print("Saving...")
    output_path = save_projections(all_players, sport, season, week)
    print(f"  Wrote {output_path}")

    return output_path
