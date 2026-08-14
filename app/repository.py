"""Per-league data access layer (Phase 0 storage seam of docs/roadmap.md).

League-scoped reads go through a LeagueDataRepository obtained from
get_repository(league_id). Today every backend reads the existing JSON
snapshots (the Yahoo league's data/raw/ files, data/raw/sleeper/{id}/ for
Sleeper leagues); when a database arrives it becomes another backend behind
the same interface and call sites don't change.

Two APIs live here, on purpose (Phase 5 step 3):

- The **dict API** (rosters/draft_years/standings/rankings) that every
  consumer still uses. Its "normalized shape" is only a docstring, and the
  backends visibly disagree inside it -- Yahoo rosters carry teamId/ownerName,
  Sleeper's carry rosterId/ownerId/starters/records; Sleeper standings have no
  rank field at all. That gap is where this project's silent-wrong-output bugs
  have lived.
- The **typed API** (roster_teams/drafts/standing_rows/ranking_rows), which
  returns app/domain.py dataclasses carrying resolved `canonical_player_id`
  and `franchise_id`. It is implemented once on the base class in terms of the
  dict methods, so no backend can drift from it, and consumers migrate one at
  a time rather than in one rewrite.

Dict shapes each backend still serves underneath:
- rosters(): [{'teamName': str, 'players': [{'playerId','playerName','position','team',...}]}]
- draft_years(): {year: [{'round','pick','playerName','team',...}, ...]}
- standings(year): [{'team','wins','losses',...}] or None when unsaved
- rankings(): market rankings [{'playerName','position','team','ranking',...}]
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from . import espn_manager, sleeper_manager, yahoo_store
from .domain import (
    BracketSource,
    DraftPick,
    Matchup,
    MatchupSide,
    PlayoffMatch,
    RankingRow,
    RosterEntry,
    RosterTeam,
    StandingRow,
    Transaction,
    TransactionMove,
    TransactionPickMove,
    _float,
    _int,
    _str,
)
from .draft_history import load_draft_years
from .draft_picks import load_draft_pick_origins, load_draft_picks
from .league_registry import League, get_league
from .player_registry import dedupe_rows_by_name
from .paths import RANKINGS_COMBINED_FILE, RAW_STANDINGS_DIR, YAHOO_LEAGUE_ROSTERS_JSON
from .standings import load_standings
from .strategy import load_yahoo_rankings


def _canonical_id(identity) -> Optional[str]:
    return identity.canonical_id if identity else None


class LeagueDataRepository:
    """Interface. draft_picks/draft_pick_origins (traded-pick ownership) may
    legitimately be None for platforms that don't track them."""

    def __init__(self, league: League):
        self.league = league

    def rosters(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def draft_years(self) -> Dict[int, List[dict]]:
        raise NotImplementedError

    def standings_years(self) -> List[int]:
        raise NotImplementedError

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError

    def rankings(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def raw_transactions(self) -> List[Dict[str, Any]]:
        """Platform-native transaction dicts (Phase 5 step 6). Default `[]`,
        not NotImplementedError -- unlike rosters/drafts/standings, a platform
        genuinely not tracking transactions yet (ESPN, Yahoo -- see
        app/domain.py's TRANSACTION_TYPES docstring) is a real, permanent
        state, not a backend that forgot to implement something."""
        return []

    def raw_matchups(self) -> Dict[str, List[Dict[str, Any]]]:
        """{week_str: [platform-native per-team rows]} (Phase 5 step 7).
        Default `{}` for the same reason raw_transactions() defaults to `[]`
        -- a platform/season with no scored weeks yet is real, not missing."""
        return {}

    def raw_playoffs(self) -> Dict[str, List[Dict[str, Any]]]:
        """{'winnersBracket': [...], 'losersBracket': [...]} (Phase 5 step 8).
        Default both empty -- a platform not tracking playoffs, or a league
        with no bracket generated yet, is a real state, not a resolution gap."""
        return {'winnersBracket': [], 'losersBracket': []}

    # ---- Typed API (Phase 5 step 3) -------------------------------------
    # Implemented ONCE here, in terms of the dict methods above, rather than
    # per backend. A backend therefore cannot drift from the contract by
    # forgetting a field -- which is exactly how the dict "contract" failed,
    # since it only ever existed in a docstring. Backends stay responsible for
    # reading their own storage; normalization happens in one place.
    #
    # These attach the identity keys from Phase 5 steps 1 and 2. Resolution is
    # best-effort: an unresolved player or franchise yields None rather than an
    # error, because a freshly imported league legitimately has neither
    # registry built yet.

    def _players(self):
        from .player_store import get_registry  # pylint: disable=import-outside-toplevel
        try:
            return get_registry()
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def _franchises(self):
        from .franchise_store import get_registry  # pylint: disable=import-outside-toplevel
        try:
            return get_registry(self.league, self)
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def _resolve_identity(self, players, name, team=None, position=None):
        """The resolved PlayerIdentity (not just its canonical id) so callers
        that also want status/injury_status don't do a second lookup."""
        if players is None or not name:
            return None
        return players.resolve(name, team=team, position=position)

    def _current_season_byes(self):
        from .bye_weeks import bye_week_map  # pylint: disable=import-outside-toplevel
        from .nfl_stats import current_nfl_season  # pylint: disable=import-outside-toplevel
        try:
            return bye_week_map(current_nfl_season())
        except Exception:  # pylint: disable=broad-exception-caught
            return {}

    def _roster_entry(self, players, byes: Dict[str, int], row: Dict[str, Any]) -> RosterEntry:
        name = _str(row.get('playerName')) or ''
        position = _str(row.get('position'))
        nfl_team = _str(row.get('team'))
        identity = self._resolve_identity(players, name, nfl_team, position)
        # A team defense has no bye of its own to look up, and byes is keyed
        # by NFL team code anyway -- this reads the same map either way.
        bye = byes.get((identity.team if identity else nfl_team) or '')
        return RosterEntry(
            name=name,
            position=position,
            nfl_team=nfl_team,
            platform_player_id=_str(row.get('playerId')),
            canonical_player_id=identity.canonical_id if identity else None,
            status=identity.status if identity else None,
            injury_status=identity.injury_status if identity else None,
            bye_week=bye,
            selected_position=_str(row.get('selectedPosition')),
            draft_round=_int(row.get('draftRound')),
            draft_pick=_int(row.get('draftPick')),
            draft_slot=_int(row.get('draftSlot')),
            raw=row,
        )

    def roster_teams(self) -> List[RosterTeam]:
        players, franchises = self._players(), self._franchises()
        byes = self._current_season_byes()
        teams = []
        for row in self.rosters():
            team_name = _str(row.get('teamName')) or ''
            # rosterId (Sleeper/ESPN) and teamId (Yahoo) are the same idea
            # under two names -- one of the divergences this type exists to end.
            platform_team_id = _str(row.get('rosterId')) or _str(row.get('teamId'))
            teams.append(RosterTeam(
                team_name=team_name,
                franchise_id=franchises.id_for_name(team_name) if franchises else None,
                manager_name=_str(row.get('managerDisplayName')) or _str(row.get('ownerName')),
                platform_team_id=platform_team_id,
                players=tuple(self._roster_entry(players, byes, p) for p in row.get('players') or []),
                starters=tuple(self._roster_entry(players, byes, p) for p in row.get('starters') or []),
                raw=row,
            ))
        return teams

    def draft(self, season: int) -> List[DraftPick]:
        return self.drafts().get(season, [])

    def drafts(self) -> Dict[int, List[DraftPick]]:
        players, franchises = self._players(), self._franchises()
        out: Dict[int, List[DraftPick]] = {}
        for season, picks in self.draft_years().items():
            typed = []
            for row in picks:
                team_name = _str(row.get('team'))
                name = _str(row.get('playerName'))
                position = _str(row.get('position'))
                typed.append(DraftPick(
                    season=int(season),
                    round=_int(row.get('round')) or 0,
                    pick=_int(row.get('pick')),
                    team_name=team_name,
                    franchise_id=franchises.id_for_name(team_name) if franchises else None,
                    player_name=name,
                    # Draft history carries no position for the Yahoo league,
                    # so the registry is what supplies one -- including for
                    # team defenses, which nflverse rosters never had.
                    canonical_player_id=_canonical_id(self._resolve_identity(players, name, position=position)),
                    position=position,
                    platform_player_id=_str(row.get('playerId')),
                    raw=row,
                ))
            out[int(season)] = typed
        return out

    def standing_rows(self, season: int) -> Optional[List[StandingRow]]:
        rows = self.standings(season)
        if rows is None:
            return None
        franchises = self._franchises()
        typed = []
        for index, row in enumerate(rows, start=1):
            team_name = _str(row.get('team')) or ''
            typed.append(StandingRow(
                season=int(season),
                # Sleeper/ESPN standings carry no rank field at all; the
                # backend already sorts them, so position in the list IS the
                # rank. Yahoo's explicit rank wins where present.
                rank=_int(row.get('rank')) or index,
                team_name=team_name,
                franchise_id=franchises.id_for_name(team_name) if franchises else None,
                wins=_int(row.get('wins')),
                losses=_int(row.get('losses')),
                ties=_int(row.get('ties')),
                points_for=_float(row.get('pointsFor')),
                points_against=_float(row.get('pointsAgainst')),
                made_playoffs=row.get('madePlayoffs'),
                raw=row,
            ))
        return typed

    def ranking_rows(self) -> List[RankingRow]:
        players = self._players()
        rows = []
        for row in self.rankings():
            name = _str(row.get('playerName')) or ''
            position = _str(row.get('position'))
            nfl_team = _str(row.get('team'))
            rows.append(RankingRow(
                ranking=_int(row.get('ranking')),
                name=name,
                position=position,
                nfl_team=nfl_team,
                adp=_float(row.get('adp')),
                source=_str(row.get('source')),
                canonical_player_id=_canonical_id(self._resolve_identity(players, name, nfl_team, position)),
                raw=row,
            ))
        return rows

    def _roster_id_lookup(self):
        """{roster_id: (franchise_id, team_name)} -- transactions() and any
        future consumer that has a platform's own roster id (not a name) need
        this instead of franchises.by_name(), since a mid-season rename would
        otherwise attribute an OLDER transaction to the CURRENT name -- correct
        for the franchise identity, misleading as a change-log entry."""
        franchises = self._franchises()
        lookup = {}
        for team in self.rosters():
            roster_id = team.get('rosterId')
            if roster_id is None:
                continue
            franchise = franchises.by_roster_id(roster_id) if franchises else None
            lookup[roster_id] = (
                franchise.franchise_id if franchise else None,
                team.get('teamName'),
            )
        return lookup

    def transactions(self) -> List[Transaction]:
        """Trades, waiver claims and free-agent moves, normalized off
        raw_transactions(). [] for a platform not tracking them, which is a
        real state (see raw_transactions()'s docstring), not a resolution gap.
        """
        players = self._players()
        roster_lookup = self._roster_id_lookup()

        def team_for(roster_id):
            return roster_lookup.get(roster_id, (None, None))

        def player_move(action: str, player_id, roster_id) -> TransactionMove:
            identity = players.by_platform_id('sleeper', player_id) if players else None
            franchise_id, team_name = team_for(roster_id)
            return TransactionMove(
                action=action,
                player_name=identity.full_name if identity else None,
                canonical_player_id=identity.canonical_id if identity else None,
                franchise_id=franchise_id,
                team_name=team_name,
            )

        typed = []
        for row in self.raw_transactions():
            season = _int(self.league.season) or 0
            moves = tuple(
                player_move('add', player_id, roster_id)
                for player_id, roster_id in (row.get('adds') or {}).items()
            ) + tuple(
                player_move('drop', player_id, roster_id)
                for player_id, roster_id in (row.get('drops') or {}).items()
            )
            pick_moves = tuple(
                TransactionPickMove(
                    season=_int(pick.get('season')) or season,
                    round=_int(pick.get('round')) or 0,
                    from_franchise_id=team_for(pick.get('previous_owner_id'))[0],
                    from_team_name=team_for(pick.get('previous_owner_id'))[1],
                    to_franchise_id=team_for(pick.get('owner_id'))[0],
                    to_team_name=team_for(pick.get('owner_id'))[1],
                )
                for pick in row.get('draft_picks') or []
            )
            typed.append(Transaction(
                transaction_id=str(row.get('transaction_id') or ''),
                type=str(row.get('type') or ''),
                season=season,
                week=_int(row.get('leg')),
                status=_str(row.get('status')),
                processed_at=_int(row.get('status_updated') or row.get('created')),
                moves=moves,
                pick_moves=pick_moves,
                waiver_bid=_int((row.get('settings') or {}).get('waiver_bid')),
                raw=row,
            ))
        return typed

    def matchups(self, season: Optional[int] = None) -> List[Matchup]:
        """Weekly head-to-head pairings for one season (this league's current
        season when omitted), pairing raw_matchups()' per-team rows by their
        shared matchup_id.

        A week whose rows are all still 0.0 is dropped, not returned as a
        0-0 tie -- Sleeper's "current week" field (settings.leg) marks a week
        as current from the moment it STARTS, not from when it finishes, so
        the latest synced week for an in-progress or not-yet-started season is
        real rows with no scores in them yet, not a finished game. This is a
        heuristic, not a guaranteed signal (a genuine 0-0 final is
        theoretically possible and would be dropped too) -- but confirmed
        against a completed season first: 17 real weeks, no phantom all-zero
        18th, so this only ever discards a week that has not actually
        happened.
        """
        players = self._players()
        roster_lookup = self._roster_id_lookup()  # already resolves franchise_id per team
        season = season if season is not None else _int(self.league.season)

        def resolved_starters(row) -> Tuple[str, ...]:
            player_points = row.get('players_points') or {}
            ids = []
            for pid in row.get('starters') or []:
                identity = players.by_platform_id('sleeper', pid) if players else None
                ids.append(identity.canonical_id if identity else str(pid))
            return tuple(ids), player_points

        def side_for(row) -> MatchupSide:
            franchise_id, team_name = roster_lookup.get(row.get('roster_id'), (None, None))
            starter_ids, player_points = resolved_starters(row)
            return MatchupSide(
                franchise_id=franchise_id,
                team_name=team_name,
                points=_float(row.get('points')),
                player_points={str(k): v for k, v in player_points.items()},
                starter_ids=starter_ids,
            )

        typed = []
        for week_key, rows in self.raw_matchups().items():
            try:
                week = int(week_key)
            except (TypeError, ValueError):
                continue
            if not any((row.get('points') or 0) > 0 for row in rows):
                continue  # not actually played yet -- see docstring
            by_matchup: Dict[Any, List[Dict[str, Any]]] = {}
            for row in rows:
                by_matchup.setdefault(row.get('matchup_id'), []).append(row)
            for pair in by_matchup.values():
                if len(pair) != 2:
                    continue  # a bye or malformed pairing; never silently guess a side
                typed.append(Matchup(
                    season=season or 0,
                    week=week,
                    home=side_for(pair[0]),
                    away=side_for(pair[1]),
                    raw={'home': pair[0], 'away': pair[1]},
                ))
        return typed

    def playoffs(self) -> List[PlayoffMatch]:
        """Bracket structure for both the winners and losers brackets.

        Deliberately does not carry points or a resolved team NAME -- only
        franchise_id -- because it is meant to be joined against matchups()
        by (season, week, franchise_id) for those, not to duplicate them. It
        also does not carry a week number: Sleeper's bracket payload has none
        (only a round), and inferring one from playoff_week_start + round
        would silently break for any league with a bye round or a bracket
        shorter/longer than the format's default.

        A match not yet played (or a later round whose participants are
        still undecided) has franchise_id fields of None -- see
        PlayoffMatch's docstring. That is the normal state for most of a
        season, not an error, since Sleeper generates bracket STRUCTURE the
        moment playoff_teams is known, long before playoff_week_start.
        """
        roster_lookup = self._roster_id_lookup()

        def fid(roster_id):
            return roster_lookup.get(roster_id, (None, None))[0] if roster_id is not None else None

        def source_for(from_ref) -> Optional[BracketSource]:
            # {'w': match_id} = winner of that match advances here;
            # {'l': match_id} = loser of that match advances here (only
            # meaningful in the losers bracket). Never both keys at once in
            # observed payloads, but if they were, 'w' wins rather than
            # silently picking one arbitrarily via a boolean-or chain.
            if not from_ref:
                return None
            if from_ref.get('w') is not None:
                return BracketSource(match_id=_int(from_ref['w']), from_winner=True)
            if from_ref.get('l') is not None:
                return BracketSource(match_id=_int(from_ref['l']), from_winner=False)
            return None

        def build(bracket: str, rows: List[Dict[str, Any]]) -> List[PlayoffMatch]:
            typed = []
            for row in rows:
                match_id = _int(row.get('m'))
                round_num = _int(row.get('r'))
                if match_id is None or round_num is None:
                    continue  # no way to place this in the bracket; skip rather than guess
                typed.append(PlayoffMatch(
                    bracket=bracket,
                    match_id=match_id,
                    round=round_num,
                    home_franchise_id=fid(row.get('t1')),
                    away_franchise_id=fid(row.get('t2')),
                    winner_franchise_id=fid(row.get('w')),
                    loser_franchise_id=fid(row.get('l')),
                    # Home/away resolve independently: a championship match
                    # can have home coming from "winner of match 3" and away
                    # from "winner of match 4" at the same time.
                    home_from=source_for(row.get('t1_from')),
                    away_from=source_for(row.get('t2_from')),
                    determines_placement=_int(row.get('p')),
                    raw=row,
                ))
            return typed

        raw = self.raw_playoffs()
        return (build('winners', raw.get('winnersBracket') or [])
                + build('losers', raw.get('losersBracket') or []))


class YahooJsonRepository(LeagueDataRepository):
    """The original single-league data layout under data/raw/ — valid only for
    the league league_settings.json describes (frank-gore)."""

    def rosters(self) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(YAHOO_LEAGUE_ROSTERS_JSON.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def draft_years(self) -> Dict[int, List[dict]]:
        return load_draft_years()

    def standings_years(self) -> List[int]:
        if not RAW_STANDINGS_DIR.exists():
            return []
        return sorted((int(p.stem) for p in RAW_STANDINGS_DIR.glob('*.json')), reverse=True)

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        return load_standings(year)

    def rankings(self) -> List[Dict[str, Any]]:
        return load_yahoo_rankings()

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        return load_draft_picks(year)

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        return load_draft_pick_origins(year)


class YahooDbRepository(LeagueDataRepository):
    """The hand-curated Yahoo league data, read from the database.

    Replaces YahooJsonRepository because data/raw/ is gitignored and the
    deploy's filesystem is ephemeral, so the JSON files never reached
    production -- every page backed by them rendered empty with no error.
    rankings() deliberately still reads the file: that board is rewritten
    daily by refresh_free_rankings(), so it self-heals on a fresh container
    the way the Sleeper/ESPN snapshots do.
    """

    @property
    def _ids(self) -> tuple:
        return self.league.platform, self.league.platform_league_id

    def rosters(self) -> List[Dict[str, Any]]:
        return yahoo_store.load_rosters(*self._ids)

    def draft_years(self) -> Dict[int, List[dict]]:
        return yahoo_store.load_draft_years(*self._ids)

    def standings_years(self) -> List[int]:
        return yahoo_store.standings_years(*self._ids)

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        return yahoo_store.load_standings(*self._ids, year)

    def rankings(self) -> List[Dict[str, Any]]:
        return load_yahoo_rankings()

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        return yahoo_store.load_draft_picks(*self._ids, year)

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        return yahoo_store.load_draft_pick_origins(*self._ids, year)


class SnapshotJsonRepository(LeagueDataRepository):
    """Shared backend over the snapshot layout sleeper_manager defines
    (rosters.json / draft_*.json / league.json under a per-league dir).
    Subclasses point `snapshots` at the platform's manager module."""

    snapshots = sleeper_manager

    @property
    def _platform_id(self) -> str:
        return self.league.platform_league_id

    def rosters(self) -> List[Dict[str, Any]]:
        return self.snapshots.load_synced_rosters(self._platform_id)

    def draft_years(self) -> Dict[int, List[dict]]:
        team_by_roster_id = {
            roster.get('rosterId'): roster.get('teamName')
            for roster in self.snapshots.load_synced_rosters(self._platform_id)
        }
        years: Dict[int, List[dict]] = {}
        for draft in self.snapshots.load_synced_drafts(self._platform_id):
            try:
                season = int(draft.get('season'))
            except (TypeError, ValueError):
                continue
            picks = [
                {**pick, 'team': team_by_roster_id.get(pick.get('rosterId'))}
                for pick in draft.get('picks') or []
            ]
            years[season] = picks
        return years

    def standings_years(self) -> List[int]:
        league = self.snapshots.load_synced_league(self._platform_id)
        if league is None:
            return []
        try:
            return [int(league.get('season'))]
        except (TypeError, ValueError):
            return []

    def standings(self, year: int) -> Optional[List[Dict[str, Any]]]:
        if year not in self.standings_years():
            return None
        rosters = sorted(
            self.snapshots.load_synced_rosters(self._platform_id),
            key=lambda r: (-(r.get('wins') or 0), r.get('losses') or 0, -(r.get('fpts') or 0)),
        )
        return [
            {
                'team': roster.get('teamName'),
                'wins': roster.get('wins'),
                'losses': roster.get('losses'),
                'ties': roster.get('ties'),
                'pointsFor': roster.get('fpts'),
                'pointsAgainst': roster.get('fptsAgainst'),
            }
            for roster in rosters
        ]

    def rankings(self) -> List[Dict[str, Any]]:
        # The shared consensus board, NOT the Yahoo league's QB-adjusted one —
        # that adjustment is derived from frank-gore's own draft history.
        try:
            payload = json.loads(RANKINGS_COMBINED_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        # Deduped for the same reason load_yahoo_rankings() is: the board can
        # carry one player twice under two spellings, and every consumer
        # downstream treats the duplicate as a second draftable human.
        return dedupe_rows_by_name(payload) if isinstance(payload, list) else []

    def draft_picks(self, year: int) -> Optional[Dict[str, Any]]:
        return {}  # snapshot platforms don't expose traded-pick ownership by round

    def draft_pick_origins(self, year: int) -> Optional[Dict[str, Any]]:
        return {}

    def raw_transactions(self) -> List[Dict[str, Any]]:
        # Base class default ([]) covers ESPN here -- espn_manager has no
        # load_synced_transactions, so the attribute lookup below would raise
        # for that subclass if this weren't guarded.
        loader = getattr(self.snapshots, 'load_synced_transactions', None)
        return loader(self._platform_id) if loader is not None else []

    def raw_matchups(self) -> Dict[str, List[Dict[str, Any]]]:
        # Same ESPN guard as raw_transactions() -- espn_manager has no
        # load_synced_matchups yet.
        loader = getattr(self.snapshots, 'load_synced_matchups', None)
        return loader(self._platform_id) if loader is not None else {}

    def raw_playoffs(self) -> Dict[str, List[Dict[str, Any]]]:
        # Same ESPN guard -- espn_manager has no load_synced_playoffs yet.
        loader = getattr(self.snapshots, 'load_synced_playoffs', None)
        return loader(self._platform_id) if loader is not None else {
            'winnersBracket': [], 'losersBracket': []}


class SleeperJsonRepository(SnapshotJsonRepository):
    snapshots = sleeper_manager


class EspnJsonRepository(SnapshotJsonRepository):
    snapshots = espn_manager


def repository_for(league: League) -> LeagueDataRepository:
    """Backend for an already resolved League (works for DB-only leagues the
    file registry doesn't know about)."""
    if league.platform == 'sleeper':
        return SleeperJsonRepository(league)
    if league.platform == 'espn':
        return EspnJsonRepository(league)
    if league.platform == 'yahoo':
        return YahooDbRepository(league)
    raise ValueError(f"No repository backend for platform '{league.platform}' (league '{league.league_id}').")


def get_repository(league_id: Optional[str] = None) -> LeagueDataRepository:
    return repository_for(get_league(league_id))
