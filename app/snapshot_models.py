"""DB-backed replacement for the data/raw/sleeper/ and data/raw/espn/ JSON
snapshot files.

Sleeper/ESPN sync is live and recurring -- the scheduler re-fetches from
each platform's API on the deployed container itself, unlike Yahoo's
one-time hand-curated import (see yahoo_migrate.py). That means the fix
here isn't a migration script run once from a machine with the files; it's
changing where the scheduler's own writes land. data/raw/ is gitignored and
Railway's filesystem is ephemeral, so every sync appeared to work (SyncRun
logged 'ok', the write succeeded in-process) but the very next redeploy or
container restart silently wiped it -- the same landmine the Yahoo data hit
before, see [[project_yahoo_data_in_db]].

One generic table, not six bespoke ones: sleeper_manager.py and
espn_manager.py already normalize every sync into the same shape
_write_json(path, payload) / _read_json(path) always used, one file per
"kind" (league/rosters/transactions/matchups/playoffs) plus a
multi-row-per-league kind (drafts, one file per draft/season). A snapshot
row keyed on (platform, platform_league_id, kind, snapshot_key) reproduces
that exactly -- snapshot_key is '' for the singleton kinds and the
draft_id/season string for drafts -- so app/snapshot_store.py can swap in
for _write_json/_read_json with the call sites in both manager modules
otherwise unchanged.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlatformSnapshot(Base):
    __tablename__ = 'platform_snapshots'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_league_id', 'kind', 'snapshot_key',
                          name='uq_platform_snapshot'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 'league' | 'rosters' | 'drafts' | 'transactions' | 'matchups' | 'playoffs'
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # '' for the singleton kinds; the draft_id/season string for 'drafts',
    # where a league can have more than one synced draft.
    snapshot_key: Mapped[str] = mapped_column(String(64), nullable=False, default='')
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
