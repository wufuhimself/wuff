"""Keeper eligibility and selection logic.

Rules: round 1/2 ineligible, 2-consecutive-season cap.
Scoring: rank first, VOR + keeper years remaining as tiebreaks.
Entry points: roster_keeper_insight(), select_best_keepers(), league_keeper_board().
Read data/config/league_rules.json before modifying.
"""
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .draft_history import consecutive_keeper_years, draft_round_for_player, load_draft_years
from .league_context import LeagueFormat
from .paths import YAHOO_RANKINGS_FILE, ensure_parent_dir
from .yahoo_client import YahooRosterPlayer

RANKINGS_FILE = YAHOO_RANKINGS_FILE


def _latest_draft_year(years: Dict[int, List[dict]]) -> Optional[int]:
    return max(years) if years else None


def _draft_history_round(player_name: str, years: Dict[int, List[dict]]) -> Optional[int]:
    latest_year = _latest_draft_year(years)
    if latest_year is None:
        return None
    return draft_round_for_player(player_name, latest_year, years)


def _keeper_streak(player_name: str, years: Dict[int, List[dict]], league_format: LeagueFormat) -> int:
    """How many consecutive seasons ending at the latest saved draft year this player was
    already kept (occupied an end-of-draft keeper slot). 0 if they weren't a keeper last season."""
    latest_year = _latest_draft_year(years)
    if latest_year is None:
        return 0
    return consecutive_keeper_years(player_name, latest_year, years=years, slots=league_format.keeper_slots)


def _keeper_years_remaining(streak: int, league_format: LeagueFormat) -> int:
    """How many more times (including a decision made this offseason) this player could still
    be kept before hitting the league's consecutive-keeper cap. Doesn't affect eligibility this
    year by itself -- it's a tiebreaker signal: keeping a player who still has years left retains
    their value longer than keeping an equally-good player who'd be capped out after this season."""
    return max(0, league_format.keeper_max_consecutive_seasons - streak)


def _keeper_cap_status(
    player_name: str,
    league_format: LeagueFormat,
    years: Dict[int, List[dict]],
) -> Optional[Tuple[bool, str]]:
    """Hard override: a player who has already occupied a keeper slot for the league's max
    consecutive seasons cannot be kept again, regardless of round eligibility.
    A cap of 0 (or negative) means the league has no consecutive-season cap at all."""
    if league_format.keeper_max_consecutive_seasons <= 0:
        return None
    streak = _keeper_streak(player_name, years, league_format)
    if streak >= league_format.keeper_max_consecutive_seasons:
        return False, (
            f'Ineligible keeper: kept {streak} consecutive season(s) already, at the '
            f'{league_format.keeper_max_consecutive_seasons}-season cap for this league.'
        )
    return None


def _normalize_name(value: str) -> str:
    return ' '.join(value.strip().lower().split())


def _normalize_position(value: str) -> str:
    if not value:
        return 'UNK'
    match = re.match(r'[A-Za-z/]+', value.strip())
    normalized = match.group(0).upper() if match else value.strip().upper()
    if normalized in {'DST', 'D/ST'}:
        return 'DEF'
    return normalized


def save_yahoo_rankings(rankings: List[Dict[str, Any]], path: Path = RANKINGS_FILE) -> None:
    ensure_parent_dir(path)
    path.write_text(json.dumps(rankings, indent=2))


def load_yahoo_rankings(path: Path = RANKINGS_FILE) -> List[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _find_ranking_for_player(player_id: str, player_name: str, rankings: List[Dict[str, Any]]) -> Optional[int]:
    normalized_name = _normalize_name(player_name)

    for item in rankings:
        if item.get('playerId') == player_id:
            return item.get('ranking')
        if normalized_name and _normalize_name(str(item.get('playerName', ''))) == normalized_name:
            return item.get('ranking')
    return None


def _expected_draft_round(ranking: int, teams: int = 12) -> int:
    return max(1, (ranking + teams - 1) // teams)


def _keeper_status(
    keeper_round: Optional[int],
    league_format: LeagueFormat,
    costless: bool = False,
) -> tuple[Optional[bool], str]:
    if keeper_round is None:
        # No draft-round record for this player -- most likely a waiver-wire pickup or
        # in-season free-agent add, never a live draft pick. The early-round restriction only
        # applies to players actually drafted that early, so an undrafted player was never
        # subject to it and defaults to eligible, valued on current ranking like anyone else.
        msg = (
            'Eligible: no draft-round record found (likely a waiver-wire pickup), so '
            'the early-round keeper restriction does not apply.'
        )
        return True, msg
    if keeper_round in league_format.keeper_ineligible_round_set:
        return False, f'Ineligible keeper: players drafted in round {keeper_round} cannot be kept in this league.'
    if keeper_round in league_format.keeper_slot_round_set:
        if costless:
            return True, 'Eligible: was a keeper last season and is still under the consecutive-season cap.'
        return None, 'This player occupied a prior-year keeper slot last season.'
    return True, 'Eligible based on last season draft round.'


def _resolve_keeper_status(
    player: YahooRosterPlayer,
    league_format: LeagueFormat,
    years: Dict[int, List[dict]],
) -> tuple[Optional[bool], str]:
    if player.keeperEligibleOverride is not None:
        if player.keeperNote:
            return player.keeperEligibleOverride, player.keeperNote
        if player.keeperEligibleOverride:
            return True, 'Eligible via manual keeper override.'
        return False, 'Ineligible via manual keeper override.'

    cap_status = _keeper_cap_status(player.playerName, league_format, years)
    if cap_status is not None:
        return cap_status

    draft_round = player.draftRound if player.draftRound is not None else _draft_history_round(player.playerName, years)
    costless = not _uses_draft_round_as_keeper_cost(league_format)
    return _keeper_status(draft_round, league_format, costless=costless)


def _uses_non_standard_keeper_cost(
    player: YahooRosterPlayer,
    league_format: LeagueFormat,
    keeper_round: Optional[int] = None,
) -> bool:
    round_value = keeper_round if keeper_round is not None else player.draftRound
    return bool(player.keeperEligibleOverride) and round_value in league_format.keeper_slot_round_set


def _manual_market_note(player: YahooRosterPlayer) -> str:
    if player.marketRoundOverride is None:
        return ''
    base = f'Manual market override: treat this player as roughly round {player.marketRoundOverride} in this league.'
    if player.valueNote:
        return f'{base} {player.valueNote}'
    return base


def _uses_draft_round_as_keeper_cost(league_format: LeagueFormat) -> bool:
    return bool(league_format.keeper_cost_uses_draft_round)


def _league_history_round(keeper_round: Optional[int], league_format: LeagueFormat) -> Optional[int]:
    if keeper_round is None:
        return None
    if keeper_round in league_format.keeper_ineligible_round_set:
        return keeper_round
    if keeper_round in league_format.keeper_slot_round_set:
        return None
    return keeper_round


def _build_position_ranks(rankings: List[Dict[str, Any]]) -> Dict[str, Dict[str, int | str]]:
    counters: Dict[str, int] = {}
    lookup: Dict[str, Dict[str, int | str]] = {}

    for item in sorted(rankings, key=lambda row: int(row.get('ranking', 9999) or 9999)):
        player_name = str(item.get('playerName', '')).strip()
        if not player_name:
            continue
        position = _normalize_position(str(item.get('position', 'UNK')))
        counters[position] = counters.get(position, 0) + 1
        lookup[_normalize_name(player_name)] = {
            'position': position,
            'positionRank': counters[position],
        }

    return lookup


def _position_starter_cutoff(position: str, league_format: LeagueFormat) -> int:
    teams = league_format.teams
    flex_count = league_format.slot_count('FLEX')
    ppr = float(league_format.scoring.get('receptions', 0.5))
    flex_wr_share = 0.5 if ppr >= 1.0 else 0.4
    flex_rb_share = 0.35 if ppr >= 1.0 else 0.45
    flex_te_share = 0.15
    cutoffs = {
        'QB': teams * (league_format.slot_count('QB') + league_format.slot_count('SUPERFLEX')),
        'RB': math.ceil(teams * (league_format.slot_count('RB') + (flex_count * flex_rb_share))),
        'WR': math.ceil(teams * (league_format.slot_count('WR') + (flex_count * flex_wr_share))),
        'TE': math.ceil(teams * (league_format.slot_count('TE') + (flex_count * flex_te_share))),
        'DEF': teams * league_format.slot_count('DEF'),
        'K': teams * league_format.slot_count('K'),
    }
    return cutoffs.get(position, teams)


def _premium_cutoff(position: str, league_format: LeagueFormat) -> int:
    teams = league_format.teams
    if position == 'QB' and league_format.slot_count('SUPERFLEX') > 0:
        return max(1, teams // 2)
    if position in {'RB', 'WR'}:
        base = teams
        if position == 'WR' and float(league_format.scoring.get('receptions', 0.5)) >= 1.0:
            return base + max(2, math.ceil(teams / 4))
        return base
    if position == 'TE':
        return max(3, math.ceil(teams / 3))
    return teams


def _projected_pick(position: str, position_rank: int, league_format: LeagueFormat) -> int:
    flex_count = league_format.slot_count('FLEX')
    ppr = float(league_format.scoring.get('receptions', 0.5))
    flex_wr_share = 0.5 if ppr >= 1.0 else 0.4
    flex_rb_share = 0.35 if ppr >= 1.0 else 0.45
    flex_te_share = 0.15
    weighted_demand = {
        'QB': league_format.teams * (league_format.slot_count('QB') + league_format.slot_count('SUPERFLEX')),
        'RB': league_format.teams * (league_format.slot_count('RB') + (flex_count * flex_rb_share)),
        'WR': league_format.teams * (league_format.slot_count('WR') + (flex_count * flex_wr_share)),
        'TE': league_format.teams * (league_format.slot_count('TE') + (flex_count * flex_te_share)),
    }
    demand = max(1, weighted_demand.get(position, league_format.teams))
    total_demand = max(1, sum(weighted_demand.values()))
    return math.ceil(position_rank * total_demand / demand)


def _replacement_round(position: str, league_format: LeagueFormat) -> Optional[int]:
    starter_cutoff = _position_starter_cutoff(position, league_format)
    if starter_cutoff <= 0:
        return None
    replacement_pick = _projected_pick(position, starter_cutoff, league_format)
    return _expected_draft_round(replacement_pick, league_format.teams)


def _value_over_replacement_rounds(position: str, market_round: Optional[int], league_format: LeagueFormat) -> Optional[int]:
    if market_round is None:
        return None
    replacement_round = _replacement_round(position, league_format)
    if replacement_round is None:
        return None
    return replacement_round - market_round


def _superflex_context_note(position: str, position_rank: int, league_format: LeagueFormat) -> Optional[str]:
    starter_cutoff = _position_starter_cutoff(position, league_format)
    premium_cutoff = _premium_cutoff(position, league_format)
    projected_round = _expected_draft_round(_projected_pick(position, position_rank, league_format), league_format.teams)

    if position_rank <= premium_cutoff:
        return f'Superflex premium: {position}{position_rank}; this is early-round QB territory in a {league_format.teams}-team league.'
    if position_rank <= league_format.teams:
        return (
            f'Starter-tier superflex QB: {position}{position_rank}; usually more of a round '
            f'{projected_round} profile than a locked-in first-rounder.'
        )
    if position_rank <= starter_cutoff:
        return f'Back-end superflex starter: {position}{position_rank}; still startable, but not a premium keeper at market cost.'
    return None


def _format_context_note(position: str, position_rank: Optional[int], league_format: LeagueFormat) -> str:
    if position_rank is None:
        return 'No position rank available for this scoring format.'

    starter_cutoff = _position_starter_cutoff(position, league_format)
    premium_cutoff = _premium_cutoff(position, league_format)
    projected_round = _expected_draft_round(_projected_pick(position, position_rank, league_format), league_format.teams)

    if position == 'QB' and league_format.slot_count('SUPERFLEX') > 0:
        superflex_note = _superflex_context_note(position, position_rank, league_format)
        if superflex_note is not None:
            return superflex_note

    if position_rank <= premium_cutoff:
        return f'Format premium: {position}{position_rank}; projected around round {projected_round} in this league setup.'
    if position_rank <= starter_cutoff:
        return f'Starter-level {position}{position_rank}; projected around round {projected_round} in this league setup.'
    return f'Depth-level {position}{position_rank}; outside the usual starter cutoff of {starter_cutoff} at this position.'


def roster_keeper_insight(
    roster_players: List[YahooRosterPlayer],
    rankings: List[Dict[str, Any]],
    teams: int = 12,
    league_format: Optional[LeagueFormat] = None,
    draft_years: Optional[Dict[int, List[dict]]] = None,
) -> List[Dict[str, Any]]:
    league_format = league_format or LeagueFormat(teams=teams)
    position_ranks = _build_position_ranks(rankings)
    draft_years = draft_years if draft_years is not None else load_draft_years()
    rankings_by_name = {_normalize_name(r.get('playerName', '')): r for r in rankings}
    insight = []
    for player in roster_players:
        ranking = _find_ranking_for_player(player.playerId, player.playerName, rankings)
        player_team = player.team
        if player_team == 'UNK' and _normalize_name(player.playerName) in rankings_by_name:
            player_team = rankings_by_name[_normalize_name(player.playerName)].get('team', 'UNK')
        expected_round = _expected_draft_round(ranking, teams) if ranking is not None else None
        keeper_round = player.draftRound if player.draftRound is not None else _draft_history_round(player.playerName, draft_years)
        keeper_eligible, keeper_status = _resolve_keeper_status(player, league_format, draft_years)
        saved_rounds = None
        position_rank_info = position_ranks.get(_normalize_name(player.playerName), {})
        normalized_position = _normalize_position(player.position)
        position_rank = position_rank_info.get('positionRank') if position_rank_info else None
        starter_cutoff = _position_starter_cutoff(normalized_position, league_format)
        projected_round = None
        if isinstance(position_rank, int):
            projected_round = _expected_draft_round(_projected_pick(normalized_position, position_rank, league_format), league_format.teams)
        if player.marketRoundOverride is not None:
            projected_round = player.marketRoundOverride
        market_round = projected_round or expected_round
        replacement_round = _replacement_round(normalized_position, league_format)
        value_over_replacement = _value_over_replacement_rounds(normalized_position, market_round, league_format)
        league_history_round = _league_history_round(keeper_round, league_format)
        uses_non_standard_keeper_cost = _uses_non_standard_keeper_cost(player, league_format, keeper_round)
        keeper_streak = _keeper_streak(player.playerName, draft_years, league_format)
        keeper_years_remaining = _keeper_years_remaining(keeper_streak, league_format)
        if (
            _uses_draft_round_as_keeper_cost(league_format)
            and market_round is not None
            and keeper_round is not None
            and keeper_eligible is True
            and not uses_non_standard_keeper_cost
        ):
            saved_rounds = market_round - keeper_round

        insight.append(
            {
                'playerId': player.playerId,
                'playerName': player.playerName,
                'position': player.position,
                'team': player_team,
                'ranking': ranking,
                'keeperRound': keeper_round,
                'expectedRound': expected_round,
                'formatExpectedRound': projected_round,
                'positionRank': position_rank,
                'starterCutoff': starter_cutoff,
                'marketRound': market_round,
                'replacementRound': replacement_round,
                'valueOverReplacementRounds': value_over_replacement,
                'leagueHistoryRound': league_history_round,
                'keeperYearsRemaining': keeper_years_remaining,
                'keeperEligible': keeper_eligible,
                'keeperLocked': player.keeperLockedOverride,
                'keeperStatus': keeper_status,
                'savedRounds': saved_rounds,
                'note': _player_note(ranking, keeper_status, _manual_market_note(player)),
            }
        )

    return sorted(
        insight,
        key=lambda item: (
            item['keeperLocked'] is not True,
            item['keeperEligible'] is not True,
            item['savedRounds'] is None,
            item['savedRounds'] is not None and -(item['savedRounds'] or 0),
            item.get('leagueHistoryRound') or 9999,
            item.get('valueOverReplacementRounds') is None,
            item.get('valueOverReplacementRounds') is not None and -(item.get('valueOverReplacementRounds') or 0),
            item.get('marketRound') or 9999,
            item['ranking'] or 9999,
        ),
    )


def select_best_keepers(
    insight: List[Dict[str, Any]],
    keeper_count: int = 2,
    league_format: Optional[LeagueFormat] = None,
    preferred_keeper_names: Optional[List[str]] = None,
    excluded_keeper_names: Optional[List[str]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    excluded_set = {_normalize_name(name) for name in (excluded_keeper_names or [])}

    locked = [item for item in insight if item.get('keeperLocked') is True]
    eligible = [
        item
        for item in insight
        if item.get('keeperEligible') is True
        and item.get('keeperLocked') is not True
        and _normalize_name(item.get('playerName', '')) not in excluded_set
    ]

    costless = league_format is not None and not _uses_draft_round_as_keeper_cost(league_format)
    if costless:
        # No keeper cost in this league, so ignore draft round entirely and pick on current
        # player strength. valueOverReplacementRounds already accounts for this league's own
        # roster construction (SUPERFLEX/FLEX counts, 3 WR spots, etc.), so a player who's
        # scarce relative to this league's starting lineup outranks one who's merely
        # high on a generic overall board. Overall ranking breaks ties within the same
        # positional value tier.
        eligible.sort(
            key=lambda item: (
                # Trust the market's overall ranking first -- it already prices in talent,
                # risk, and role. VOR (positional scarcity for this league's roster shape)
                # only breaks ties between players who are close in ranking; it shouldn't let
                # a much lower-ranked scarce-position player (e.g. a TE6 or backup QB) leapfrog
                # a clearly better overall player just because their position is thin.
                item.get('ranking') or 9999,
                item.get('valueOverReplacementRounds') is None,
                item.get('valueOverReplacementRounds') is not None and -(item.get('valueOverReplacementRounds') or 0),
                # Tiebreak only, and only between players who are otherwise about equal:
                # a player with more consecutive-keeper years left retains that value longer.
                -(item.get('keeperYearsRemaining') or 0),
            )
        )
    else:
        eligible.sort(
            key=lambda item: (
                item.get('savedRounds') is None,
                item.get('savedRounds') is not None and -(item.get('savedRounds') or 0),
                item.get('leagueHistoryRound') or 9999,
                item.get('valueOverReplacementRounds') is None,
                item.get('valueOverReplacementRounds') is not None and -(item.get('valueOverReplacementRounds') or 0),
                item.get('marketRound') or item.get('formatExpectedRound') or item.get('expectedRound') or 9999,
                item.get('ranking') or 9999,
            )
        )

    chosen = locked[:keeper_count]
    remaining_slots = max(0, keeper_count - len(chosen))

    # If preferred keepers specified, use those to fill remaining slots
    if preferred_keeper_names and remaining_slots > 0:
        preferred_set = {name.strip().lower() for name in preferred_keeper_names}
        preferred_matches = [
            item for item in eligible
            if ' '.join(item.get('playerName', '').lower().split()) in preferred_set
            and item.get('playerId') not in {c.get('playerId') for c in chosen}
        ]
        chosen.extend(preferred_matches[:remaining_slots])
        remaining_slots = max(0, remaining_slots - len(preferred_matches))

    # Fill remaining slots from eligible (auto-selected)
    if remaining_slots > 0:
        auto_selected = [
            item for item in eligible
            if item.get('playerId') not in {c.get('playerId') for c in chosen}
        ]
        chosen.extend(auto_selected[:remaining_slots])

    alternates = [
        item for item in eligible
        if item.get('playerId') not in {selected.get('playerId') for selected in chosen}
    ]
    return chosen, alternates


def _player_note(ranking: Optional[int], keeper_status: str, manual_market_note: str) -> str:
    if ranking is None:
        return 'No ranking data.'
    if manual_market_note:
        return manual_market_note
    return keeper_status


def league_keeper_board(
    league_rosters: List[Dict[str, Any]],
    rankings: List[Dict[str, Any]],
    league_format: LeagueFormat,
    keeper_count: int = 2,
    keeper_prefs_override: Optional[Dict[str, List[str]]] = None,
    keeper_excludes_override: Optional[Dict[str, List[str]]] = None,
    draft_years: Optional[Dict[int, List[dict]]] = None,
    include_file_prefs: bool = True,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Compute the best keepers for every team in a saved league-roster snapshot.

    keeper_excludes_override: {team_name: [player_name, ...]} of players the user has
      explicitly unchecked -- forbidden from auto-fill/alternates for that team, same
      per-team shape as keeper_prefs_override.

    Returns (per_team, remaining_board):
      per_team: one entry per team with its chosen keepers and alternates.
      remaining_board: rankings with every chosen keeper removed, sorted by ranking, with a
        gap-free 'draftOrder' field added (the CSV `keepers-board` writes is exactly this).
    """
    def normalize(name: str) -> str:
        return ' '.join(name.strip().lower().split())

    # Config-file keeper preferences (the original Yahoo league's hand-set
    # intents). Generic league boards pass include_file_prefs=False so another
    # league's identically named team can't inherit them.
    keeper_prefs_file = Path(__file__).parent.parent / 'data' / 'config' / 'keeper_preferences.json'
    keeper_prefs = {}
    if include_file_prefs and keeper_prefs_file.exists():
        with open(keeper_prefs_file, encoding='utf-8') as f:
            keeper_prefs = json.load(f)

    per_team = []
    chosen_names: set = set()
    for team in league_rosters:
        team_name = str(team.get('teamName', '')).rsplit(' - ', maxsplit=1)[-1]
        players = [
            YahooRosterPlayer(
                playerId=str(p.get('playerId', '')).strip(),
                playerName=str(p.get('playerName', '')).replace('player Notes', '').strip(),
                position=p.get('position', 'UNK'),
                team=p.get('team', 'UNK'),
                status=None,
                selectedPosition=None,
                eligibleSlots=None,
                draftRound=p.get('draftRound'),
                draftPick=p.get('draftPick'),
                draftSlot=p.get('draftSlot'),
                keeperEligibleOverride=p.get('keeperEligibleOverride'),
                keeperLockedOverride=p.get('keeperLockedOverride'),
                marketRoundOverride=p.get('marketRoundOverride'),
                valueNote=p.get('valueNote'),
                keeperNote=p.get('keeperNote'),
            )
            for p in team.get('players', [])
            if isinstance(p, dict)
        ]
        insight = roster_keeper_insight(players, rankings, teams=league_format.teams,
                                        league_format=league_format, draft_years=draft_years)
        # User marks (keeper_prefs_override) beat the config-file prefs for a team.
        preferred_names = (keeper_prefs_override or {}).get(team_name) or keeper_prefs.get(team_name)
        excluded_names = (keeper_excludes_override or {}).get(team_name)
        chosen, alternates = select_best_keepers(
            insight, keeper_count=keeper_count, league_format=league_format,
            preferred_keeper_names=preferred_names,
            excluded_keeper_names=excluded_names,
        )
        # Every keeper-eligible player regardless of exclude state, in the same
        # rank order select_best_keepers uses -- lets a UI build a fixed
        # candidate list that doesn't lose/reorder players as the user
        # includes/excludes them (an excluded player is still eligible, just
        # not auto-picked). locked players aren't included since they aren't
        # a real choice for the user to toggle.
        eligible_pool = sorted(
            (item for item in insight
             if item.get('keeperEligible') is True and item.get('keeperLocked') is not True),
            key=lambda item: item.get('ranking') if item.get('ranking') is not None else 9999,
        )
        per_team.append({
            'team': team_name, 'chosen': chosen, 'alternates': alternates, 'eligiblePool': eligible_pool,
        })
        chosen_names.update(normalize(c['playerName']) for c in chosen)

    remaining = sorted(
        (r for r in rankings if normalize(str(r.get('playerName', ''))) not in chosen_names),
        key=lambda r: r.get('ranking') or 9999,
    )

    position_ranks = _build_position_ranks(rankings)

    # Add posRank to per_team chosen, alternates, and eligiblePool
    for entry in per_team:
        for player_list in [entry['chosen'], entry['alternates'], entry['eligiblePool']]:
            for player in player_list:
                player_name = str(player.get('playerName', ''))
                position = _normalize_position(str(player.get('position', 'UNK')))
                position_rank_info = position_ranks.get(_normalize_name(player_name), {})
                position_rank = position_rank_info.get('positionRank')
                if position_rank:
                    player['posRank'] = f'{position}{position_rank}'

    remaining_board = []
    for i, r in enumerate(remaining, start=1):
        row = dict(r)
        row['draftOrder'] = i
        player_name = str(r.get('playerName', ''))
        position = _normalize_position(str(r.get('position', 'UNK')))
        position_rank_info = position_ranks.get(_normalize_name(player_name), {})
        position_rank = position_rank_info.get('positionRank')
        if position_rank:
            row['posRank'] = f'{position}{position_rank}'
        remaining_board.append(row)

    return per_team, remaining_board


def forecast_opponent_keepers(
    league_rosters: List[Dict[str, Any]],
    rankings: List[Dict[str, Any]],
    league_format: LeagueFormat,
    *,
    your_roster_players: Optional[List[YahooRosterPlayer]] = None,
    keeper_count: int = 2,
    consider_top: int = 100,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Forecast keepers using each team's ACTUAL roster and real eligibility rules (round 1/2
    excluded, consecutive-season cap enforced) -- not just 'top ranked players not on my roster',
    which would surface plainly ineligible players (round-1 picks, capped-out repeat keepers)
    as if they were about to be kept.

    Returns (likely_keepers, top_available):
      likely_keepers: every OTHER team's actual best-N keeper picks (your own team excluded
        if your_roster_players is given -- you already know your own keepers).
      top_available: the league-wide remaining draft board (all real keepers removed),
        capped at consider_top.
    """
    per_team, remaining_board = league_keeper_board(league_rosters, rankings, league_format, keeper_count=keeper_count)

    your_team_name = None
    if your_roster_players:
        your_ids = {p.playerId for p in your_roster_players if p.playerId}
        your_names = {' '.join(p.playerName.strip().lower().split()) for p in your_roster_players}
        best_overlap = 0
        for team in league_rosters:
            team_ids = {str(p.get('playerId', '')).strip() for p in team.get('players', []) if isinstance(p, dict)}
            team_names = {
                ' '.join(str(p.get('playerName', '')).replace('player Notes', '').strip().lower().split())
                for p in team.get('players', [])
                if isinstance(p, dict)
            }
            overlap = len(your_ids & team_ids) + len(your_names & team_names)
            if overlap > best_overlap:
                best_overlap = overlap
                your_team_name = str(team.get('teamName', '')).rsplit(' - ', maxsplit=1)[-1]

    likely_keepers = []
    for entry in per_team:
        if your_team_name is not None and entry['team'] == your_team_name:
            continue
        for c in entry['chosen']:
            row = dict(c)
            row['team'] = entry['team']
            likely_keepers.append(row)

    return likely_keepers, remaining_board[:consider_top]
