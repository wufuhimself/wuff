"""ORM models for multi-user state: accounts and per-user league follows.

DbLeague is the DB-side league record for leagues imported through the web
onboarding flow; to_registry_league() bridges it to the League dataclass so
app/repository.py can serve its data without knowing where the record came
from (leagues.json for the local single-user setup, DB rows for web users).
"""
from datetime import datetime, timezone
from typing import Optional

from flask_login import UserMixin
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .league_context import LeagueFormat
from .league_registry import League


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(UserMixin, Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    leagues = relationship('UserLeague', back_populates='user', cascade='all, delete-orphan')


class DbLeague(Base):
    __tablename__ = 'leagues'
    __table_args__ = (UniqueConstraint('platform', 'platform_league_id', name='uq_platform_league'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    season: Mapped[Optional[str]] = mapped_column(String(8))
    total_teams: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    followers = relationship('UserLeague', back_populates='league', cascade='all, delete-orphan')

    def to_registry_league(self) -> League:
        # Imported leagues are visibility-only until their keeper/roster rules
        # are configured, so keeper logic stays off (keeper_slots=0).
        return League(
            league_id=self.slug,
            platform=self.platform,
            platform_league_id=self.platform_league_id,
            name=self.name,
            season=self.season,
            format=LeagueFormat(
                teams=self.total_teams or 12,
                league_id=self.platform_league_id,
                league_name=self.name,
                keeper_slots=0,
                keeper_ineligible_rounds=[],
                keeper_slot_rounds=[],
            ),
        )


class UserLeague(Base):
    __tablename__ = 'user_leagues'
    __table_args__ = (UniqueConstraint('user_id', 'league_id', name='uq_user_league'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    league_id: Mapped[int] = mapped_column(ForeignKey('leagues.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user = relationship('User', back_populates='leagues')
    league = relationship('DbLeague', back_populates='followers')
