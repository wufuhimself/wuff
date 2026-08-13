"""Dated snapshots of the rankings board, so movement over time is visible.

The daily refresh (app/free_rankings.py) overwrites its output files, so
without this nothing records that a player was ADP 1.0 last week and 1.2 now.
Every refresh appends one snapshot here; deltas are computed against the most
recent *earlier* snapshot.

Storage is one JSON file per day under data/raw/rankings/history/{YYYY-MM-DD}.json,
rather than a DB table: it's shared app-wide (not per-user), it's naturally
append-only, and keeping it on disk means the trend data survives a DB reset
and can be inspected or diffed by hand. Same-day refreshes overwrite that
day's file, so running the refresh five times in an afternoon doesn't create
five near-identical "days".

Snapshot shape: {"date": "YYYY-MM-DD", "capturedAt": iso8601,
                 "players": [{playerName, position, team, ranking, adp}, ...]}

DEPLOYMENT CAVEAT: data/raw/ is gitignored, and hosts like Fly/Railway give a
container an ephemeral filesystem -- these snapshots would be wiped on every
restart, silently resetting all trend data to "no history yet". Before the
Phase 4 deploy (docs/roadmap.md) this needs either a persistent volume or a
move into the database. Flagged when the module was written (2026-08-11),
because the failure mode is silent: trends just stop rendering.
"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import RAW_RANKINGS_DIR, ensure_parent_dir
from .player_registry import index_rows_by_name
from .player_registry import normalize_name as _normalize

RANKING_HISTORY_DIR = RAW_RANKINGS_DIR / 'history'


def snapshot_path(day: Optional[date] = None) -> Path:
    return RANKING_HISTORY_DIR / f"{(day or date.today()).isoformat()}.json"


def save_snapshot(rankings: List[Dict[str, Any]], day: Optional[date] = None) -> Path:
    """Record today's board. Overwrites an existing same-day snapshot."""
    path = snapshot_path(day)
    ensure_parent_dir(path)
    payload = {
        'date': (day or date.today()).isoformat(),
        'capturedAt': datetime.now().isoformat(timespec='seconds'),
        'players': [
            {
                'playerName': entry.get('playerName'),
                'position': entry.get('position'),
                'team': entry.get('team'),
                'ranking': entry.get('ranking'),
                'adp': entry.get('adp'),
            }
            for entry in rankings
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def snapshot_dates() -> List[str]:
    """Every snapshot date on disk, newest first."""
    if not RANKING_HISTORY_DIR.exists():
        return []
    return sorted((p.stem for p in RANKING_HISTORY_DIR.glob('*.json')), reverse=True)


def load_snapshot(day: str) -> Optional[Dict[str, Any]]:
    path = RANKING_HISTORY_DIR / f'{day}.json'
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def previous_snapshot(before: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The most recent snapshot strictly older than `before` (default: today).

    This is what "last week" means in practice -- whatever the previous
    capture was. Returns None until there are at least two snapshots, which is
    why trend arrows simply don't render on a fresh install rather than
    claiming everything is flat.
    """
    cutoff = before or date.today().isoformat()
    earlier = [d for d in snapshot_dates() if d < cutoff]
    return load_snapshot(earlier[0]) if earlier else None


def movement_since(previous: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{normalized_player_name: {prevRanking, prevAdp, prevDate}} for comparison.

    Empty dict when there's no earlier snapshot, so callers can treat "no
    history yet" and "no movement" as distinguishable states.
    """
    if not previous:
        return {}
    # Collision-safe: a snapshot can hold the same player twice under two
    # spellings ("James Cook III" at 20, "James Cook" at 257). Keyed naively,
    # the duplicate wins and yesterday's rank reads as 257 -- so a flat player
    # renders as "up 237", a wrong number presented with full confidence.
    return {
        name: {
            'prevRanking': entry.get('ranking'),
            'prevAdp': entry.get('adp'),
            'prevDate': previous.get('date'),
        }
        for name, entry in index_rows_by_name(previous.get('players', [])).items()
    }


def annotate_with_movement(
    rankings: List[Dict[str, Any]], previous: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Add prevRanking/rankingDelta/prevAdp/adpDelta/trend to each row.

    rankingDelta is positive when a player has IMPROVED (moved toward pick 1),
    so the sign reads the way a reader expects an arrow to: +3 means "up 3".
    adpDelta keeps ADP's own convention (lower is earlier), so a rising ADP
    number is a player drifting later -- trend is derived from ranking, which
    is the field the board is actually sorted by.

    Players absent from the previous snapshot get trend 'new'.
    """
    prev = previous if previous is not None else previous_snapshot()
    moved = movement_since(prev)
    if not moved:
        return [{**row, 'trend': None} for row in rankings]

    annotated = []
    for row in rankings:
        entry = dict(row)
        before = moved.get(_normalize(row.get('playerName', '')))
        if before is None:
            entry.update({'prevRanking': None, 'rankingDelta': None,
                          'prevAdp': None, 'adpDelta': None, 'trend': 'new'})
            annotated.append(entry)
            continue

        prev_rank, cur_rank = before['prevRanking'], row.get('ranking')
        rank_delta = (prev_rank - cur_rank) if (prev_rank is not None and cur_rank is not None) else None
        prev_adp, cur_adp = before['prevAdp'], row.get('adp')
        adp_delta = round(cur_adp - prev_adp, 2) if (prev_adp is not None and cur_adp is not None) else None

        entry.update({
            'prevRanking': prev_rank,
            'rankingDelta': rank_delta,
            'prevAdp': prev_adp,
            'adpDelta': adp_delta,
            'prevDate': before['prevDate'],
            'trend': 'flat' if not rank_delta else ('up' if rank_delta > 0 else 'down'),
        })
        annotated.append(entry)
    return annotated
