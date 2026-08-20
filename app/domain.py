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
from datetime import datetime, timezone
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
class DraftSchedule:
    """When a league's draft is (or was), independent of whether it happened.

    Separate from DraftPick rather than a field on it: a scheduled draft has
    no picks at all, which is precisely the state worth surfacing -- keeper
    decisions matter *before* the draft, and by the time picks exist it is too
    late to act on them. Kept out of repository.draft_years() for the same
    reason: that dict means "seasons this league has actually drafted," and an
    empty season in it moves _next_draft_season() forward a year.

    Sleeper-only in practice today. ESPN's snapshot has no draft-date field
    wrapped, and Yahoo is still blocked on API access -- both correctly return
    no schedules rather than a guess.
    """
    season: int
    # Sleeper's own values: 'pre_draft', 'drafting', 'in_progress', 'complete',
    # 'paused'. Not narrowed to an enum -- an unrecognized status should show
    # through as itself rather than be coerced into a wrong known one.
    status: Optional[str] = None
    # Scheduled start, timezone-aware UTC. None when the league has set no
    # date yet, which is a real and common state for a pre_draft league.
    starts_at: Optional[datetime] = None
    draft_type: Optional[str] = None
    platform_draft_id: Optional[str] = None
    pick_count: int = 0
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)

    @property
    def has_drafted(self) -> bool:
        """Whether picks actually exist. Derived from pick_count, not status:
        the status string is the platform's word for it and this is the thing
        callers actually branch on."""
        return self.pick_count > 0

    def days_until(self, now: Optional[datetime] = None) -> Optional[int]:
        """Whole days from `now` until the draft starts; negative once past,
        None with no scheduled date. Rounded toward zero, so 'in 0 days'
        means today rather than 'already happened'."""
        if self.starts_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (self.starts_at - now).days


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


@dataclass(frozen=True)
class MatchupSide:
    """One team's side of one weekly matchup.

    `points` is the platform's OWN computed total, not one wuff derives from
    LeagueFormat.scoring -- Sleeper's real scoring settings run to ~130 rule
    keys (IDP tackles, special-teams return yards, per-bracket defense
    bonuses) against LeagueFormat's 9. Recomputing would silently diverge
    from what the league itself sees on any rule this project doesn't model;
    trusting the platform's number is the correct scope, not a shortcut.
    """
    franchise_id: Optional[str]
    team_name: Optional[str]
    points: Optional[float]
    # {canonical_player_id or platform_player_id: points}. Canonical where the
    # player resolved, the raw platform id as a fallback key so a starter's
    # score is never silently dropped just because the name didn't resolve.
    player_points: Mapping[str, float] = field(default_factory=dict)
    starter_ids: Tuple[str, ...] = ()  # canonical where resolved, else platform id


@dataclass(frozen=True)
class Matchup:
    """One head-to-head pairing for one week. Two Matchups with the same
    week and non-tuple identity would be the same real-world game reported
    twice -- callers that want "all games in week N" should get exactly
    teams/2 of these, which check_repository_contract.py asserts."""
    season: int
    week: int
    home: MatchupSide
    away: MatchupSide
    raw: Mapping[str, Any] = field(default=_NO_RAW, repr=False, compare=False)

    @property
    def is_tie(self) -> bool:
        return (self.home.points is not None and self.away.points is not None
                and self.home.points == self.away.points)


# 'winners' (championship bracket) or 'losers' (consolation bracket -- still
# meaningful in most leagues, since it decides who finishes last, not just
# who finishes first). Sleeper's own two brackets, kept as the canonical pair
# rather than inventing wuff's own vocabulary, same reasoning as
# TRANSACTION_TYPES.
BRACKET_TYPES = ('winners', 'losers')


@dataclass(frozen=True)
class BracketSource:
    """Where one side of a not-yet-decided PlayoffMatch will come from: the
    winner or loser of another match in the same bracket. Home and away sides
    resolve independently and can point at different matches and different
    outcomes -- a real championship slot in this codebase's own test data has
    home coming from "winner of match 3" and away from "winner of match 4",
    which a single match-level field can't represent without silently
    dropping one of the two."""
    match_id: int
    from_winner: bool  # True = winner of match_id advances here; False = loser


@dataclass(frozen=True)
class PlayoffMatch:
    """One bracket slot. Team identity and points are NOT duplicated here --
    join against Matchup by (season, week, franchise_id) for those, since a
    playoff week's games are also ordinary weekly matchups underneath; this
    type carries only the bracket STRUCTURE a Matchup has no way to express
    (round, seeding, who advances, what placement is on the line).

    A franchise_id of None means that slot is not decided yet -- either the
    match hasn't been played (season in progress) or, for a later round, the
    team that will fill it depends on an earlier match's still-unknown result
    (see `home_from`/`away_from`). Both are real, common states for an
    in-progress bracket, not errors.
    """
    bracket: str  # one of BRACKET_TYPES
    match_id: int
    round: int
    home_franchise_id: Optional[str] = None
    away_franchise_id: Optional[str] = None
    winner_franchise_id: Optional[str] = None
    loser_franchise_id: Optional[str] = None
    # Set only when that side is still TBD and depends on another match in
    # the SAME bracket. See BracketSource -- home and away resolve
    # independently.
    home_from: Optional[BracketSource] = None
    away_from: Optional[BracketSource] = None
    # The final standing this match decides, when it decides one (1st, 3rd,
    # 5th, ...). None for a match that only determines who ADVANCES, not a
    # final placement -- most non-final rounds.
    determines_placement: Optional[int] = None
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
