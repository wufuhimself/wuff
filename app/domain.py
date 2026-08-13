"""Typed domain shapes for league data (Phase 5 step 3).

`app/repository.py` has been a real seam since Phase 0, but what it serves is
untyped dicts whose "normalized shape" lives in a docstring and is enforced
by nothing. The backends genuinely disagree, and always have:

    Yahoo roster team:  teamId, teamName, ownerName, playerCount, players
    Sleeper roster team: rosterId, ownerId, managerDisplayName, teamName,
                         players, starters, wins, losses, ties, fpts, ...

    Yahoo draft pick:   round, pick, playerName, team
    Sleeper draft pick: + playerId, position, rosterId

    Yahoo standing:     rank, streak, waiverBudget, moves, madePlayoffs, ...
    Sleeper standing:   no rank at all

Every silent-wrong-output bug this project has logged lived in that gap. The
dataclasses here are the contract instead, and they carry the two identity
keys Phase 5 steps 1 and 2 established -- `canonical_player_id` and
`franchise_id` -- so a caller can join a roster to a stat line or a draft
pick to a manager without going back through display names.

`raw` on every type is the original dict. It is a **migration aid**, not part
of the design: it lets a consumer move to the typed API one field at a time
instead of in one risky rewrite. When nothing reads `.raw` any more, it goes.
"""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

# Read-only, so it is safe to share as a default across every instance --
# and a plain {} is rejected outright as a mutable dataclass default anyway.
_NO_RAW: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class RosterEntry:
    """One player on one team's roster."""
    name: str
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    # The platform's own player id. Sleeper's opaque numeric string, or -- for
    # the Yahoo league, whose rosters were paste-parsed -- the player's name.
    platform_player_id: Optional[str] = None
    # app/player_registry.py's cross-platform key. None when the name did not
    # resolve, which is a state callers must handle rather than assume away.
    canonical_player_id: Optional[str] = None
    # Roster status ('Active', 'Inactive', 'Injured Reserve') and the
    # separate injury designation ('Questionable', 'IR', 'PUP', 'NA', ...).
    # Neither comes from any platform's roster snapshot -- Yahoo's paste never
    # carried one and Sleeper's roster payload doesn't either -- both are
    # filled from app/player_registry.py, which gets them from the Sleeper
    # players cache regardless of which platform this roster belongs to.
    # None on both fields when the player did not resolve.
    status: Optional[str] = None
    injury_status: Optional[str] = None
    # This player's NFL team's bye week for the league's season, from
    # app/bye_weeks.py. None when unresolved or the season's schedule isn't
    # available yet (future weeks not yet released).
    bye_week: Optional[int] = None
    selected_position: Optional[str] = None
    draft_round: Optional[int] = None
    draft_pick: Optional[int] = None
    draft_slot: Optional[int] = None
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)


@dataclass(frozen=True)
class RosterTeam:
    """One team's current roster."""
    team_name: str
    franchise_id: Optional[str] = None
    manager_name: Optional[str] = None
    platform_team_id: Optional[str] = None
    players: Tuple[RosterEntry, ...] = ()
    # Sleeper/ESPN report a starting lineup; Yahoo's paste does not, so this
    # is empty there rather than guessed at from position eligibility.
    starters: Tuple[RosterEntry, ...] = ()
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)


@dataclass(frozen=True)
class DraftPick:
    """One pick in one season's draft."""
    season: int
    round: int
    pick: Optional[int] = None
    team_name: Optional[str] = None
    franchise_id: Optional[str] = None
    player_name: Optional[str] = None
    canonical_player_id: Optional[str] = None
    position: Optional[str] = None
    platform_player_id: Optional[str] = None
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)

    def overall(self, teams: int) -> Optional[int]:
        """Overall pick number. `pick` is already overall in Sleeper's payload
        and per-round in Yahoo's, so this cannot be precomputed without knowing
        which -- callers pass their league's team count and get the value under
        the per-round reading, matching what keeper_service/draft_analysis
        already compute by hand."""
        if self.pick is None or not teams:
            return None
        return (self.round - 1) * teams + self.pick


@dataclass(frozen=True)
class StandingRow:
    """One team's finish in one season."""
    season: int
    rank: int
    team_name: str
    franchise_id: Optional[str] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    points_for: Optional[float] = None
    points_against: Optional[float] = None
    made_playoffs: Optional[bool] = None
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)


@dataclass(frozen=True)
class RankingRow:
    """One player on the market board."""
    ranking: Optional[int]
    name: str
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    adp: Optional[float] = None
    source: Optional[str] = None
    canonical_player_id: Optional[str] = None
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)


# 'trade' | 'waiver' | 'free_agent' | 'commissioner'. Sleeper's own vocabulary
# (see app/sleeper_manager.py); kept as the canonical set rather than
# reinvented, since it is the only platform this resolves for today (Phase 5
# step 6) and a future ESPN/Yahoo importer maps onto it rather than the other
# way around.
TRANSACTION_TYPES = ('trade', 'waiver', 'free_agent', 'commissioner')


@dataclass(frozen=True)
class TransactionMove:
    """One player added or dropped by one team, inside one Transaction.

    A trade with 2 players changing hands is 2 TransactionMoves (one add, one
    drop) per side -- 4 total -- not one row with two player lists, because a
    waiver claim is naturally one add + one drop and a pure free-agent add is
    one move alone; a single shape covers all three without a null-heavy
    trade-only variant.
    """
    action: str  # 'add' | 'drop'
    player_name: Optional[str] = None
    canonical_player_id: Optional[str] = None
    franchise_id: Optional[str] = None
    team_name: Optional[str] = None


@dataclass(frozen=True)
class TransactionPickMove:
    """One draft pick changing hands inside a trade."""
    season: int
    round: int
    from_franchise_id: Optional[str] = None
    from_team_name: Optional[str] = None
    to_franchise_id: Optional[str] = None
    to_team_name: Optional[str] = None


@dataclass(frozen=True)
class Transaction:
    """One roster move: a trade, waiver claim, free-agent add/drop, or
    commissioner action.

    `week` is the platform's own reporting week, which is not always the
    calendar week the move happened in -- Sleeper buckets pre-season and
    in-season free-agent moves into week 0 and week 1 respectively, for
    instance. Use `processed_at` for a real timestamp.
    """
    transaction_id: str
    type: str  # one of TRANSACTION_TYPES
    season: int
    week: Optional[int]
    status: Optional[str] = None
    processed_at: Optional[int] = None  # epoch milliseconds, platform's own clock
    moves: Tuple[TransactionMove, ...] = ()
    pick_moves: Tuple[TransactionPickMove, ...] = ()
    waiver_bid: Optional[int] = None
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
