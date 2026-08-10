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
    # Web-configured LeagueFormat overrides (keeper rules etc.) as a JSON
    # object; merged over the registry/default format by league_service.
    rules_json: Mapped[Optional[str]] = mapped_column(String(4000))
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


class SyncRun(Base):
    """One snapshot-sync attempt for one league. Keyed by platform ids, not a
    DbLeague FK, because scheduled syncs also cover the local
    sleeper_leagues.json leagues that have no DB row."""
    __tablename__ = 'sync_runs'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='running')
    detail: Mapped[Optional[str]] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EspnCredential(Base):
    """A user's espn_s2/SWID cookie pair for one private ESPN league, stored
    encrypted (app/crypto.py). Sync uses any stored credential for a league;
    cookies are per-ESPN-account, so each user keeps their own row."""
    __tablename__ = 'espn_credentials'
    __table_args__ = (UniqueConstraint('user_id', 'platform_league_id', name='uq_espn_credential'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    espn_s2_encrypted: Mapped[str] = mapped_column(String(2000), nullable=False)
    swid_encrypted: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KeeperMark(Base):
    """A user-marked keeper decision: 'this team is keeping this player.'
    Feeds the keeper board as an override on top of the computed picks, and
    marked players are removed from the post-keeper draft board."""
    __tablename__ = 'keeper_marks'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_league_id', 'team_name', 'player_name', name='uq_keeper_mark'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserLeague(Base):
    __tablename__ = 'user_leagues'
    __table_args__ = (UniqueConstraint('user_id', 'league_id', name='uq_user_league'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    league_id: Mapped[int] = mapped_column(ForeignKey('leagues.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user = relationship('User', back_populates='leagues')
    league = relationship('DbLeague', back_populates='followers')
