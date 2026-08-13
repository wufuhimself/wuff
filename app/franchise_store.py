"""Persistence + process cache for franchise identity (Phase 5 step 2).

app/franchise_registry.py builds and resolves; this module stores and serves.
Same split as player_registry/player_store and yahoo_models/yahoo_store.

Cached per league rather than globally: a franchise id is only meaningful
inside its own league, and the leagues a request touches are few.
"""
import logging
from typing import Dict, List, Optional, Tuple

from .db import SessionLocal, init_db
from .franchise_models import FranchiseName, FranchiseRecord
from .franchise_registry import Franchise, FranchiseRegistry, build_franchises

logger = logging.getLogger(__name__)

_cache: Dict[Tuple[str, str], FranchiseRegistry] = {}


def reset_cache() -> None:
    _cache.clear()


def _to_franchise(row: FranchiseRecord, names: List[str]) -> Franchise:
    return Franchise(
        franchise_id=row.franchise_id,
        platform=row.platform,
        platform_league_id=row.platform_league_id,
        name=row.name,
        manager_display_name=row.manager_display_name,
        owner_id=row.owner_id,
        roster_id=row.roster_id,
        names=names or [row.name],
        source=row.source,
    )


def save_franchises(platform: str, platform_league_id: str, franchises: List[Franchise]) -> int:
    """Replace this league's stored franchises. Scoped delete, not a global
    wipe -- rebuilding one league must not drop another's."""
    init_db()
    platform_league_id = str(platform_league_id)
    with SessionLocal() as session:
        session.query(FranchiseName).filter_by(
            platform=platform, platform_league_id=platform_league_id).delete()
        session.query(FranchiseRecord).filter_by(
            platform=platform, platform_league_id=platform_league_id).delete()
        session.bulk_save_objects([
            FranchiseRecord(
                franchise_id=f.franchise_id,
                platform=f.platform,
                platform_league_id=f.platform_league_id,
                name=f.name[:255],
                manager_display_name=f.manager_display_name,
                owner_id=f.owner_id,
                roster_id=f.roster_id,
                source=f.source,
            )
            for f in franchises
        ])
        session.bulk_save_objects([
            FranchiseName(
                franchise_id=f.franchise_id,
                platform=f.platform,
                platform_league_id=f.platform_league_id,
                name=name[:255],
            )
            for f in franchises
            for name in sorted(set(f.names or [f.name]))
        ])
        session.commit()
    _cache.pop((platform, platform_league_id), None)
    return len(franchises)


def load_franchises(platform: str, platform_league_id: str) -> List[Franchise]:
    init_db()
    platform_league_id = str(platform_league_id)
    with SessionLocal() as session:
        rows = session.query(FranchiseRecord).filter_by(
            platform=platform, platform_league_id=platform_league_id).all()
        name_rows = session.query(FranchiseName).filter_by(
            platform=platform, platform_league_id=platform_league_id).all()
    names: Dict[str, List[str]] = {}
    for name_row in name_rows:
        names.setdefault(name_row.franchise_id, []).append(name_row.name)
    return [_to_franchise(row, names.get(row.franchise_id, [])) for row in rows]


def get_registry(league, repo=None, allow_build: bool = True) -> FranchiseRegistry:
    """This league's franchise registry, from the database when it is there.

    Falls back to building in memory (and says so in the log) so the CLI, the
    gate script and a freshly deployed container all work before the first
    `build-franchises` run. An empty registry resolves nothing, which would
    degrade silently, so the fallback matters more than the tidiness.
    """
    key = (league.platform, str(league.platform_league_id))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    franchises = load_franchises(*key)
    if not franchises and allow_build and repo is not None:
        franchises = build_franchises(repo, league)
        logger.info('franchises: none stored for %s/%s, built %s in memory '
                    '(run `python3 -m app build-franchises` to persist)',
                    key[0], key[1], len(franchises))
    registry = FranchiseRegistry(franchises)
    _cache[key] = registry
    return registry


def rebuild_league(league, repo) -> Dict[str, int]:
    franchises = build_franchises(repo, league)
    written = save_franchises(league.platform, str(league.platform_league_id), franchises)
    sources: Dict[str, int] = {}
    for franchise in franchises:
        sources[franchise.source] = sources.get(franchise.source, 0) + 1
    return {'written': written, **sources}


def backfill_keeper_marks(league, repo) -> Dict[str, int]:
    """Stamp franchise_id onto this league's existing keeper marks.

    Rows whose team name no longer matches any franchise are LEFT ALONE with
    a NULL franchise_id rather than being guessed at -- they keep working via
    name matching, and the count is reported so the gap is visible. That is
    the whole point of adding the column alongside team_name instead of
    replacing it.
    """
    # Imported here, not at module scope: models imports franchise_models,
    # and a top-level import back into models would close the cycle.
    from .models import KeeperMark  # pylint: disable=import-outside-toplevel

    registry = get_registry(league, repo)
    platform, platform_league_id = league.platform, str(league.platform_league_id)
    stamped = unresolved = 0
    with SessionLocal() as session:
        rows = session.query(KeeperMark).filter_by(
            platform=platform, platform_league_id=platform_league_id).all()
        for row in rows:
            franchise_id = registry.id_for_name(row.team_name)
            if franchise_id:
                row.franchise_id = franchise_id
                stamped += 1
            elif not row.franchise_id:
                unresolved += 1
        session.commit()
    return {'marks': len(rows), 'stamped': stamped, 'unresolved': unresolved}


def franchise_id_for_team(league, repo, team_name: Optional[str]) -> Optional[str]:
    """Convenience for call sites that only have a display name."""
    if not team_name:
        return None
    return get_registry(league, repo).id_for_name(team_name)
