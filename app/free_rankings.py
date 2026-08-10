"""Daily rankings/ADP refresh from free, licensing-safe sources.

Replaces the manual FantasyPros CSV import (removed 2026-08-10 — FantasyPros
data can't be redistributed on a public site). Sources:

- FantasyFootballCalculator's free ADP API — real market data from thousands
  of live mock drafts, refreshed continuously. This is the primary board.
- The local Sleeper players cache — extends the tail beyond FFC's draftable
  pool using Sleeper's search_rank ordering, and fills missing team info.

refresh_free_rankings() writes the three files the rest of wuff already
reads, so no consumer changes:
- data/raw/rankings/yahoo_rankings.json  — the working board (with the
  frank-gore QB historical adjustment applied on top when draft history is
  available; see app/qb_historical_adjustment.py)
- data/raw/rankings/rankings_combined.json — the pure market board
- data/raw/adp/adp_combined.json — ADP lookup for board enrichment

Scheduled daily by app/sync_scheduler.py; manual run:
`python3 -m app refresh-free-rankings`.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .adp_manager import normalize_player_name, save_adp_json
from .paths import RANKINGS_COMBINED_FILE, ensure_parent_dir
from .qb_historical_adjustment import apply_qb_historical_adjustment, compute_historical_qb_pick_targets
from .sleeper_manager import load_players_cache
from .strategy import save_yahoo_rankings

FFC_ADP_URL = 'https://fantasyfootballcalculator.com/api/v1/adp/{scoring}'
SLEEPER_TAIL_LIMIT = 300
_RANKABLE_POSITIONS = {'QB', 'RB', 'WR', 'TE', 'DEF', 'PK', 'K'}


def fetch_ffc_adp(scoring: str = 'ppr', teams: int = 12, year: Optional[int] = None) -> List[Dict[str, Any]]:
    """Raw FFC ADP entries, already sorted by ADP."""
    params = {'teams': teams, 'year': year or datetime.now().year}
    resp = requests.get(FFC_ADP_URL.format(scoring=scoring), params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    players = payload.get('players') or []
    return sorted(players, key=lambda p: p.get('adp') or 9999)


def _normalize_position(position: str) -> str:
    upper = (position or 'UNK').upper()
    if upper in {'DST', 'D/ST', 'DEF'}:
        return 'DEF'
    if upper == 'PK':
        return 'K'
    return upper


def _sleeper_tail(seen_names: set, limit: int = SLEEPER_TAIL_LIMIT) -> List[Dict[str, Any]]:
    """Active Sleeper players not already ranked, ordered by search_rank, to
    give the board depth past FFC's draftable pool."""
    candidates = []
    for player_id, info in load_players_cache().items():
        if not isinstance(info, dict) or info.get('active') is not True:
            continue
        position = _normalize_position(info.get('position') or '')
        search_rank = info.get('search_rank')
        name = (info.get('full_name') or '').strip()
        if position not in _RANKABLE_POSITIONS or not name or not isinstance(search_rank, int):
            continue
        if search_rank >= 9999999 or normalize_player_name(name) in seen_names:
            continue
        candidates.append({
            'playerId': str(player_id),
            'playerName': name,
            'position': position,
            'team': info.get('team') or 'UNK',
            'searchRank': search_rank,
        })
    candidates.sort(key=lambda entry: entry['searchRank'])
    return candidates[:limit]


def build_free_rankings(scoring: str = 'ppr', teams: int = 12, year: Optional[int] = None) -> List[Dict[str, Any]]:
    """FFC ADP order, extended with a Sleeper search-rank tail. Uniform shape:
    {playerId, playerName, position, team, ranking, adp?, source}."""
    rankings: List[Dict[str, Any]] = []
    seen_names: set = set()

    for entry in fetch_ffc_adp(scoring=scoring, teams=teams, year=year):
        name = (entry.get('name') or '').strip()
        if not name:
            continue
        seen_names.add(normalize_player_name(name))
        rankings.append({
            'playerId': str(entry.get('player_id', '')),
            'playerName': name,
            'position': _normalize_position(entry.get('position') or ''),
            'team': entry.get('team') or 'UNK',
            'ranking': len(rankings) + 1,
            'adp': entry.get('adp'),
            'source': 'ffc_adp',
        })

    for entry in _sleeper_tail(seen_names):
        rankings.append({
            'playerId': entry['playerId'],
            'playerName': entry['playerName'],
            'position': entry['position'],
            'team': entry['team'],
            'ranking': len(rankings) + 1,
            'source': 'sleeper_search',
        })

    return rankings


def refresh_free_rankings(scoring: str = 'ppr', teams: int = 12, year: Optional[int] = None) -> Dict[str, Any]:
    """Fetch, write all three consumer files, apply the QB adjustment to the
    working board when league draft history allows. Returns a summary dict."""
    rankings = build_free_rankings(scoring=scoring, teams=teams, year=year)
    if not rankings:
        raise RuntimeError('Free rankings refresh produced an empty board; keeping existing files.')

    ensure_parent_dir(RANKINGS_COMBINED_FILE)
    RANKINGS_COMBINED_FILE.write_text(json.dumps(rankings, indent=2))

    adp_entries = [
        {
            'playerName': normalize_player_name(entry['playerName']),
            'adp': entry['adp'],
            'platforms': {'ffc': entry['adp']},
            'original': f"{entry['playerName']} {entry['team']}",
        }
        for entry in rankings
        if entry.get('adp') is not None
    ]
    save_adp_json(adp_entries)

    qb_adjusted = False
    working_board = rankings
    targets = compute_historical_qb_pick_targets()
    if targets:
        working_board = apply_qb_historical_adjustment(rankings, targets=targets)
        qb_adjusted = True
    save_yahoo_rankings(working_board)

    ffc_count = sum(1 for entry in rankings if entry['source'] == 'ffc_adp')
    return {
        'total': len(rankings),
        'ffc': ffc_count,
        'sleeperTail': len(rankings) - ffc_count,
        'adpEntries': len(adp_entries),
        'qbAdjusted': qb_adjusted,
    }
