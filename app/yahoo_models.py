"""ORM models for the hand-curated Yahoo league data that used to live only
as JSON files under data/raw/.

Why these exist: data/raw/ is gitignored and container filesystems are
ephemeral, so the deployed app had none of the Frank Gore league's draft
history, standings, pick ownership, or rosters — pages rendered empty with
no error. Unlike the Sleeper/ESPN snapshots (re-synced from their APIs) and
the rankings board (rewritten daily by refresh_free_rankings), this data has
no upstream to re-fetch from: Yahoo API access is still blocked, and every
row was entered by hand over months. The database is the only place it
survives a deploy.

Scoped by platform + platform_league_id (the KeeperMark/BoardAdjustment
convention) rather than hardcoded to Yahoo, so a second manually-curated
league needs no schema change.

Deliberately NOT modelled here:
- data/raw/rankings/yahoo_rankings.json — regenerated daily by
  refresh_free_rankings(); self-heals on deploy.
- data/raw/managers/, data/raw/season_rosters/ — read only by
  keeper_history.py's CLI commands, never by the web app, so they are not
  part of the production gap. Still local-only; see docs/roadmap.md.
- data/raw/rosters/yahoo_roster.json — legacy single-team MCP artifact,
  superseded by yahoo_league_rosters.json.
"""
# pylint: disable=unsubscriptable-object
# astroid stops inferring SQLAlchemy's Mapped[...] for the whole module once
# a relationship() appears in it, flagging every annotated column. Confirmed
# a false positive by removing the two relationship() calls (the errors go
# away with no other change); scripts/compare_yahoo_backends.py exercises
# these models against the real data.
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class YahooDraftPick(Base):
    """One pick from one season's draft (data/raw/draft_history/{year}.json)."""
    __tablename__ = 'yahoo_draft_picks'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_league_id', 'year', 'round', 'pick',
                         name='uq_yahoo_draft_pick'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    pick: Mapped[int] = mapped_column(Integer, nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    team: Mapped[str] = mapped_column(String(255), nullable=False)


class YahooDraftPickOwnership(Base):
    """How many picks a team owns in a round for an upcoming draft, and which
    original snake slots they came from post-trade
    (data/raw/draft_picks/{year}.json). origins_json is a JSON list of team
    names -- kept as a list column rather than its own table because it is
    always read as one unit per (team, round) and never queried by element."""
    __tablename__ = 'yahoo_draft_pick_ownership'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_league_id', 'year', 'team_name', 'round',
                         name='uq_yahoo_pick_ownership'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    pick_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # None means this team had no picksByRoundOrigins key at all, which
    # load_draft_pick_origins() treats differently from an empty list.
    origins_json: Mapped[Optional[str]] = mapped_column(String(2000))


class YahooStanding(Base):
    """One team's final finish for one season (data/raw/standings/{year}.json).

    made_playoffs and note only exist from 2025 on. The read path omits
    None-valued optional fields rather than emitting explicit nulls: no
    standings row in the source JSON has ever held a real null, and
    standings.current_team_names() does row.get('note', '') then regexes the
    result -- an emitted None would crash it where an absent key does not.
    """
    __tablename__ = 'yahoo_standings'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_league_id', 'year', 'team_name',
                         name='uq_yahoo_standing'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    wins: Mapped[Optional[int]] = mapped_column(Integer)
    losses: Mapped[Optional[int]] = mapped_column(Integer)
    ties: Mapped[Optional[int]] = mapped_column(Integer)
    points_for: Mapped[Optional[float]] = mapped_column(Float)
    points_against: Mapped[Optional[float]] = mapped_column(Float)
    streak: Mapped[Optional[str]] = mapped_column(String(16))
    waiver_budget: Mapped[Optional[int]] = mapped_column(Integer)
    waiver_priority: Mapped[Optional[int]] = mapped_column(Integer)
    moves: Mapped[Optional[int]] = mapped_column(Integer)
    made_playoffs: Mapped[Optional[bool]] = mapped_column(Boolean)
    note: Mapped[Optional[str]] = mapped_column(String(500))


class YahooRosterTeam(Base):
    """A team in the current-season roster snapshot
    (data/raw/rosters/yahoo_league_rosters.json). Rewritten wholesale by
    parse-rosters, so the whole table is replaced per league on save rather
    than merged."""
    __tablename__ = 'yahoo_roster_teams'
    __table_args__ = (
        UniqueConstraint('platform', 'platform_league_id', 'team_name',
                         name='uq_yahoo_roster_team'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_league_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(Integer)
    owner_name: Mapped[Optional[str]] = mapped_column(String(255))
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored rather than derived from len(players): it is a field of the
    # source snapshot, and a mismatch would be real information.
    player_count: Mapped[Optional[int]] = mapped_column(Integer)
    # Source-order index, so round-tripping preserves team order.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    players = relationship(
        'YahooRosterPlayerRow',
        back_populates='roster_team',
        cascade='all, delete-orphan',
        order_by='YahooRosterPlayerRow.sort_order',
    )


class YahooRosterPlayerRow(Base):
    """One player on a YahooRosterTeam. Every field of the source player dict
    is a column, including the hand-set override/note fields -- all 15 keys
    are present on every row in the source JSON (verified), several
    explicitly null, so the read path emits all of them rather than omitting
    nulls."""
    __tablename__ = 'yahoo_roster_players'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    roster_team_id: Mapped[int] = mapped_column(ForeignKey('yahoo_roster_teams.id'), nullable=False, index=True)
    # Source-order index, so round-tripping preserves roster order.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    player_id: Mapped[Optional[str]] = mapped_column(String(64))
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    player_position: Mapped[Optional[str]] = mapped_column(String(16))
    team: Mapped[Optional[str]] = mapped_column(String(8))
    status: Mapped[Optional[str]] = mapped_column(String(32))
    selected_position: Mapped[Optional[str]] = mapped_column(String(16))
    eligible_slots_json: Mapped[Optional[str]] = mapped_column(String(255))
    draft_round: Mapped[Optional[int]] = mapped_column(Integer)
    draft_pick: Mapped[Optional[int]] = mapped_column(Integer)
    draft_slot: Mapped[Optional[int]] = mapped_column(Integer)
    keeper_eligible_override: Mapped[Optional[bool]] = mapped_column(Boolean)
    keeper_locked_override: Mapped[Optional[bool]] = mapped_column(Boolean)
    market_round_override: Mapped[Optional[int]] = mapped_column(Integer)
    value_note: Mapped[Optional[str]] = mapped_column(String(500))
    keeper_note: Mapped[Optional[str]] = mapped_column(String(500))

    roster_team = relationship('YahooRosterTeam', back_populates='players')
