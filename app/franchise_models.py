"""ORM models for franchise identity (Phase 5 step 2).

Derived like the player registry, not curated: rebuilt wholesale from synced
snapshots plus the hand-authored alias file, so a rebuild may delete and
re-insert every row. The one thing that is NOT derived is
`KeeperMark.franchise_id` (in app/models.py) -- that is real user data
pointing at these rows.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FranchiseRecord(Base):
    """One team slot in one league.

    `franchise_id` is `{platform}:{league}:owner:{id}` or `...:roster:{id}`
    where the platform gives us one, and `{platform}:{league}:{slug}` for
    Yahoo, whose slug comes from the alias file when it covers the team and
    from the team's own canonical name when it doesn't.
    """
    __tablename__ = 'franchises'
    __table_args__ = (UniqueConstraint('franchise_id', name='uq_franchise_id'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    franchise_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manager_display_name: Mapped[Optional[str]] = mapped_column(String(255))
    owner_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    roster_id: Mapped[Optional[str]] = mapped_column(String(32))
    # Which signal established this identity: owner_id | roster_id |
    # alias_file | name. 'name' means "unresolved, one season's display name
    # standing in for a franchise" -- visible incompleteness, not a claim.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default='name')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class FranchiseName(Base):
    """One display name a franchise has worn. Many rows per franchise: this is
    the rename history that makes a keeper mark survive a team rename."""
    __tablename__ = 'franchise_names'
    __table_args__ = (UniqueConstraint('franchise_id', 'name', name='uq_franchise_name'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    franchise_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
