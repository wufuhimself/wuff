"""Persistence + process cache for the player registry (Phase 5 step 1).

app/player_registry.py builds and resolves; this module stores and serves.
Same split as yahoo_models/yahoo_store.

The registry is derived data, so `save_registry()` is a full replace rather
than an upsert — there is nothing to preserve, and a partial rebuild would
leave stale aliases pointing at players a source has since dropped.

`get_registry()` caches in the process. ~12k rows is trivial to hold and far
too slow to re-read per web request; call `reset_cache()` after a rebuild.
"""
import logging
from typing import List, Optional

from .db import SessionLocal, init_db
from .player_models import PlayerAlias, PlayerRecord
from .player_registry import PlayerIdentity, PlayerRegistry, build_identities

logger = logging.getLogger(__name__)

_cached_registry: Optional[PlayerRegistry] = None  # pylint: disable=invalid-name


def _to_identity(row: PlayerRecord) -> PlayerIdentity:
    return PlayerIdentity(
        canonical_id=row.canonical_id,
        full_name=row.full_name,
        search_name=row.search_name,
        position=row.position,
        team=row.team,
        status=row.status,
        injury_status=row.injury_status,
        active=bool(row.active),
        sleeper_id=row.sleeper_id,
        yahoo_id=row.yahoo_id,
        espn_id=row.espn_id,
        gsis_id=row.gsis_id,
        sources=tuple((row.sources or '').split(',')) if row.sources else (),
    )


def save_registry(identities: List[PlayerIdentity]) -> int:
    """Replace the stored registry. Returns the row count written."""
    init_db()
    with SessionLocal() as session:
        session.query(PlayerAlias).delete()
        session.query(PlayerRecord).delete()
        session.bulk_save_objects([
            PlayerRecord(
                canonical_id=identity.canonical_id,
                full_name=identity.full_name[:255],
                search_name=identity.search_name[:255],
                position=identity.position,
                team=identity.team,
                status=identity.status,
                injury_status=identity.injury_status,
                active=identity.active,
                sleeper_id=identity.sleeper_id,
                yahoo_id=identity.yahoo_id,
                espn_id=identity.espn_id,
                gsis_id=identity.gsis_id,
                sources=','.join(identity.sources) or None,
            )
            for identity in identities
        ])
        # Persist the index the in-memory registry actually built -- curated
        # aliases and suffix-preserving keys included -- rather than
        # recomputing it here, where the two could drift apart.
        session.bulk_save_objects([
            PlayerAlias(alias=alias[:255], canonical_id=canonical_id)
            for alias, canonical_id in PlayerRegistry(identities).alias_pairs()
        ])
        session.commit()
    reset_cache()
    return len(identities)


def load_identities() -> List[PlayerIdentity]:
    init_db()
    with SessionLocal() as session:
        return [_to_identity(row) for row in session.query(PlayerRecord).all()]


def reset_cache() -> None:
    global _cached_registry  # pylint: disable=global-statement
    _cached_registry = None


def get_registry(allow_file_fallback: bool = True) -> PlayerRegistry:
    """The registry every caller should use.

    Reads the database first. When it is empty and file fallback is allowed,
    builds in memory from whatever sources are on disk and logs that it did —
    that keeps the CLI and the gate script working before the first
    `build-player-registry` run, without ever *silently* serving a registry
    the database doesn't have. An empty registry resolves nothing, which is
    the one failure mode that has to stay loud.
    """
    global _cached_registry  # pylint: disable=global-statement
    if _cached_registry is not None:
        return _cached_registry

    identities = load_identities()
    if not identities and allow_file_fallback:
        identities, stats = build_identities()
        logger.warning(
            'player registry: database empty, built %s identities from local files '
            '(run `python3 -m app build-player-registry` to persist)', stats['total'])
    _cached_registry = PlayerRegistry(identities)
    return _cached_registry


def rebuild_registry() -> dict:
    """Build from local sources and persist. Returns the build stats."""
    identities, stats = build_identities()
    if not identities:
        # Nothing on disk to build from. Keep whatever is stored rather than
        # wiping a good registry because a cache file went missing.
        logger.warning('player registry: no source data found, keeping stored registry')
        stats['written'] = 0
        return stats
    stats['written'] = save_registry(identities)
    return stats
