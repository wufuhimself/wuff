"""Load the hand-curated Yahoo JSON files under data/raw/ into the database.

Runs against whatever DATABASE_URL points at -- local SQLite for a dev
setup, Railway's Postgres for the real one (the JSON files only exist on a
local checkout, so this always runs *from* a machine that has them, never
inside the deployed container).

Idempotent: each source replaces its own scope (a season, or the whole
roster snapshot) rather than appending, matching the "the whole file is
authoritative" semantics the JSON layout always had. Re-running against
unchanged files is a no-op in effect.

Also the shared loader behind the per-source `import-*` CLI commands, so
adding next season's draft history uses the same code path this migration
does rather than a second one that can drift.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from . import yahoo_store
from .db import SessionLocal, init_db
from .paths import RAW_DRAFT_HISTORY_DIR, RAW_DRAFT_PICKS_DIR, RAW_STANDINGS_DIR, YAHOO_LEAGUE_ROSTERS_JSON

# The default league these files describe. data/config/leagues.json maps
# 'frank-gore' to this pair; the tables are keyed by it so a second
# hand-curated league can land later without a schema change.
DEFAULT_PLATFORM = 'yahoo'
DEFAULT_PLATFORM_LEAGUE_ID = '9410'


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def import_draft_history(platform: str, platform_league_id: str,
                         directory: Path = RAW_DRAFT_HISTORY_DIR) -> Dict[int, int]:
    """{year: picks written}."""
    written: Dict[int, int] = {}
    if not directory.exists():
        return written
    with SessionLocal() as session:
        for path in sorted(directory.glob('*.json')):
            payload = _read_json(path)
            if payload is None:
                continue
            year = payload.get('year')
            picks = payload.get('picks')
            if not isinstance(year, int) or not isinstance(picks, list):
                continue
            written[year] = yahoo_store.save_draft_year(
                platform, platform_league_id, year, picks, session=session,
            )
        session.commit()
    return written


def import_draft_picks(platform: str, platform_league_id: str,
                       directory: Path = RAW_DRAFT_PICKS_DIR) -> Dict[int, int]:
    """{year: ownership rows written}."""
    written: Dict[int, int] = {}
    if not directory.exists():
        return written
    with SessionLocal() as session:
        for path in sorted(directory.glob('*.json')):
            payload = _read_json(path)
            if payload is None:
                continue
            year = payload.get('year')
            teams = payload.get('teams')
            if not isinstance(year, int) or not isinstance(teams, list):
                continue
            written[year] = yahoo_store.save_draft_pick_ownership(
                platform, platform_league_id, year, teams, session=session,
            )
        session.commit()
    return written


def import_standings(platform: str, platform_league_id: str,
                     directory: Path = RAW_STANDINGS_DIR) -> Dict[int, int]:
    """{year: standings rows written}."""
    written: Dict[int, int] = {}
    if not directory.exists():
        return written
    with SessionLocal() as session:
        for path in sorted(directory.glob('*.json')):
            payload = _read_json(path)
            if payload is None:
                continue
            year = payload.get('year')
            standings = payload.get('standings')
            if not isinstance(year, int) or not isinstance(standings, list):
                continue
            written[year] = yahoo_store.save_standings(
                platform, platform_league_id, year, standings, session=session,
            )
        session.commit()
    return written


def import_rosters(platform: str, platform_league_id: str,
                   path: Path = YAHOO_LEAGUE_ROSTERS_JSON) -> Optional[int]:
    """Players written, or None when there is no roster snapshot to import."""
    payload = _read_json(path)
    if not isinstance(payload, list):
        return None
    with SessionLocal() as session:
        written = yahoo_store.save_rosters(platform, platform_league_id, payload, session=session)
        session.commit()
    return written


def migrate_all(platform: str = DEFAULT_PLATFORM,
                platform_league_id: str = DEFAULT_PLATFORM_LEAGUE_ID) -> List[str]:
    """Run every importer. Returns human-readable summary lines."""
    init_db()
    summary: List[str] = []

    drafts = import_draft_history(platform, platform_league_id)
    summary.append(
        f'draft_history: {len(drafts)} seasons, {sum(drafts.values())} picks'
        + (f" ({', '.join(f'{y}:{n}' for y, n in sorted(drafts.items()))})" if drafts else '')
    )

    picks = import_draft_picks(platform, platform_league_id)
    summary.append(
        f'draft_picks: {len(picks)} seasons, {sum(picks.values())} ownership rows'
        + (f" ({', '.join(f'{y}:{n}' for y, n in sorted(picks.items()))})" if picks else '')
    )

    standings = import_standings(platform, platform_league_id)
    summary.append(
        f'standings: {len(standings)} seasons, {sum(standings.values())} rows'
        + (f" ({', '.join(f'{y}:{n}' for y, n in sorted(standings.items()))})" if standings else '')
    )

    rosters = import_rosters(platform, platform_league_id)
    if rosters is None:
        summary.append('rosters: no snapshot found (skipped)')
    else:
        team_count = len(yahoo_store.load_rosters(platform, platform_league_id))
        summary.append(f'rosters: {team_count} teams, {rosters} players')

    return summary
