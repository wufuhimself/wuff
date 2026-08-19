"""Read/write PlatformSnapshot rows -- the DB-backed drop-in for the
data/raw/sleeper/ and data/raw/espn/ JSON files. See snapshot_models.py for
why this exists (short version: those files never survive a Railway
redeploy).

Mirrors the _write_json(path, payload) / _read_json(path) shape both
sleeper_manager.py and espn_manager.py already used, so their sync/load
functions swap the storage call and keep everything else -- including every
caller in app/repository.py and app/web.py -- unchanged.
"""
import json
from datetime import datetime, timezone
from typing import Any, List, Optional

from .db import SessionLocal
from .snapshot_models import PlatformSnapshot


def write_snapshot(platform: str, platform_league_id: str, kind: str,
                    payload: Any, key: str = '') -> None:
    """Upsert one snapshot row. key distinguishes multiple rows of the same
    kind for one league (only 'drafts' needs this -- one row per draft_id)."""
    payload_json = json.dumps(payload, ensure_ascii=False)
    with SessionLocal() as session:
        row = session.query(PlatformSnapshot).filter_by(
            platform=platform, platform_league_id=platform_league_id,
            kind=kind, snapshot_key=key,
        ).one_or_none()
        if row is None:
            row = PlatformSnapshot(
                platform=platform, platform_league_id=platform_league_id,
                kind=kind, snapshot_key=key,
            )
            session.add(row)
        row.payload_json = payload_json
        row.synced_at = datetime.now(timezone.utc)
        session.commit()


def read_snapshot(platform: str, platform_league_id: str, kind: str,
                   key: str = '') -> Optional[Any]:
    with SessionLocal() as session:
        row = session.query(PlatformSnapshot).filter_by(
            platform=platform, platform_league_id=platform_league_id,
            kind=kind, snapshot_key=key,
        ).one_or_none()
        return json.loads(row.payload_json) if row is not None else None


def read_snapshots(platform: str, platform_league_id: str, kind: str) -> List[Any]:
    """Every row of one kind for a league, most-recently-synced first --
    the DB equivalent of glob('draft_*.json') for the 'drafts' kind."""
    with SessionLocal() as session:
        rows = (
            session.query(PlatformSnapshot)
            .filter_by(platform=platform, platform_league_id=platform_league_id, kind=kind)
            .order_by(PlatformSnapshot.synced_at.desc())
            .all()
        )
        return [json.loads(row.payload_json) for row in rows]
