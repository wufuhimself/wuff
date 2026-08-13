"""ORM models for the cross-platform player registry (Phase 5 step 1).

Unlike every other table in this app, these rows are **derived, not curated**:
they are rebuilt wholesale from the Sleeper players cache and the nflverse
roster CSVs (see app/player_registry.py). Nothing here is user data, so a
rebuild is free to delete and re-insert every row.

They live in the database anyway for the same reason the Yahoo league data
does — data/raw/ is gitignored and container filesystems are ephemeral — plus
one the Yahoo tables don't have: the nflverse CSVs exist only on a local
checkout, so a locally-built registry is *richer* than one production could
build for itself. Persisting it lets the local build be pushed up the way
`migrate-yahoo-data` does.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PlayerRecord(Base):
    """One player (or one team defense) and every platform id pointing at them.

    canonical_id is `sleeper:{id}` wherever Sleeper knows the player, else
    `nfl:{gsis_id}` — see app/player_registry.py for why it is deliberately
    NOT gsis-first (a rookie's gsis id can arrive later, which would change
    the key underneath anything storing it).
    """
    __tablename__ = 'players'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    search_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    position: Mapped[Optional[str]] = mapped_column(String(8))
    team: Mapped[Optional[str]] = mapped_column(String(8))
    status: Mapped[Optional[str]] = mapped_column(String(32))
    injury_status: Mapped[Optional[str]] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sleeper_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    yahoo_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    espn_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    gsis_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    # Comma-joined source names ('sleeper', 'sleeper,nflverse') — which
    # source(s) contributed this row, so a coverage gap is attributable.
    sources: Mapped[Optional[str]] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PlayerAlias(Base):
    """A normalized name key pointing at a canonical player.

    One player has several: their full name, first+last, and for team defenses
    the abbreviation, city and nickname, because ranking sources spell a
    defense four different ways. Several players can share one alias — that is
    the ambiguity resolve() refuses to guess through, not a constraint
    violation.
    """
    __tablename__ = 'player_aliases'
    __table_args__ = (UniqueConstraint('alias', 'canonical_id', name='uq_player_alias'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    canonical_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
