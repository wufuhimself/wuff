"""Keeper-board business logic, factored out of web.py (2026-08-11 refactor).

Everything here is pure computation/data-access — no Flask request/response
handling. web.py's keeper routes (keepers_board_view, league_keepers,
keeper_mark) call _keeper_board_state() as their single source of truth so
the AJAX partial-update response and the full-page render can never drift
out of sync; see that function's docstring for the shape it returns.
"""
import re
from typing import Optional

from .board_service import apply_adjustments, load_adjustments
from .db import SessionLocal
from .league_context import load_league_format
from .league_registry import get_league
from .models import KeeperMark
from .outcome_log import load_outcomes, log_outcome, save_outcomes
from .paths import YAHOO_LEAGUE_ROSTERS_JSON
from .player_registry import normalize_name
from .ranking_history import annotate_with_movement
from .repository import get_repository, repository_for
from .standings import snake_draft_order
from .strategy import league_keeper_board

KEEPER_FORECAST_METHOD_VERSION = 'web_keeper_board_v1'


def _default_league_platform_ids() -> tuple:
    league = get_league()
    return league.platform, league.platform_league_id


def load_keeper_marks(platform: Optional[str] = None, platform_league_id: Optional[str] = None) -> tuple:
    """User-set keeper decisions for a league (default league when unspecified).

    Returns (include_marks, exclude_marks), each {team: [player_name, ...]}.
    'include' forces a player onto that team's keeper board; 'exclude' forbids
    the algorithm from auto-picking a player, freeing the slot for the next
    best eligible one."""
    if platform is None or platform_league_id is None:
        platform, platform_league_id = _default_league_platform_ids()
    include_marks: dict = {}
    exclude_marks: dict = {}
    with SessionLocal() as session:
        rows = (
            session.query(KeeperMark)
            .filter_by(platform=platform, platform_league_id=platform_league_id)
            .order_by(KeeperMark.created_at)
            .all()
        )
    for row in rows:
        target = exclude_marks if row.action == 'exclude' else include_marks
        target.setdefault(row.team_name, []).append(row.player_name)
    return include_marks, exclude_marks


def team_pick_numbers(
    team_name: str, round1_order: list, live_rounds: int, teams: int, origins_by_team: Optional[dict],
) -> set:
    """Overall pick numbers (1-indexed, live rounds only) a team actually owns, honoring saved
    traded-pick origins when available; falls back to the team's own snake slot if not."""
    rounds = snake_draft_order(round1_order, live_rounds)
    picks = set()
    for round_number, order in rounds.items():
        slot_position = {t: position for position, t in enumerate(order, start=1)}
        owners = (
            origins_by_team.get(team_name, {}).get(round_number, [team_name])
            if origins_by_team is not None else [team_name]
        )
        for origin_team in owners:
            position = slot_position.get(origin_team)
            if position is not None:
                picks.add((round_number - 1) * teams + position)
    return picks


def load_adp_map() -> dict:
    """Load ADP data as dict keyed by normalized player name.

    Re-normalizes the stored key rather than trusting it: adp_combined.json is
    written with whatever normalizer was current when it was last refreshed,
    so a key format change would otherwise make every ADP lookup silently miss
    until the next daily refresh rewrote the file.
    """
    from .adp_manager import load_adp_json, normalize_player_name
    return {normalize_player_name(entry['playerName']): entry['adp'] for entry in load_adp_json()}


def enrich_with_adp(player_list, adp_map):
    """Add ADP value to player objects."""
    from .adp_manager import normalize_player_name
    for player in player_list:
        player_name = normalize_player_name(player.get('playerName', ''))
        player['adp'] = adp_map.get(player_name)


def calculate_keeper_impact(keeper_forecasts, league_format=None):  # pylint: disable=unused-argument
    """Calculate how many elite players at each position are locked up as keepers.

    Shows impact on draft board by counting HIGH confidence keepers per position.

    league_format: accepted but not yet used for the tier-size math below --
    TODO: elite tier sizes assume a ~12-team/SUPERFLEX-shaped league and will
    misrepresent impact % for a differently-sized imported league (e.g. a
    Sleeper dynasty league with a different team count/starter shape). Scaling
    this correctly is a product decision (what "elite tier" means per
    position), not a mechanical fix -- revisit when a non-12-team league
    actually needs accurate impact numbers.
    """
    # Define elite tier sizes (how many top players per position matter for strategy)
    elite_tiers = {
        'TE': 5,    # Top 5 elite TEs (scarce)
        'RB': 16,   # Top 16 elite RBs
        'WR': 20,   # Top 20 elite WRs
        'QB': 15,   # Top 15 elite QBs
    }

    # Count HIGH confidence keepers by position
    high_conf_keepers = {pos: 0 for pos in elite_tiers}

    for forecast in keeper_forecasts:
        for keeper in forecast['keepers']:
            position = keeper['position']
            if position in elite_tiers and keeper['confidence'] == 'high':
                high_conf_keepers[position] += 1

    # Build impact summary
    impact = []
    for position in ['TE', 'RB', 'WR', 'QB']:
        kept = high_conf_keepers[position]
        elite_tier_size = elite_tiers[position]
        available = elite_tier_size - kept

        if elite_tier_size > 0:
            pct_kept = round((kept / elite_tier_size) * 100, 1)

            impact.append({
                'position': position,
                'kept': kept,
                'elite_tier': elite_tier_size,
                'available': available,
                'pct_kept': pct_kept,
            })

    return sorted(impact, key=lambda x: x['pct_kept'], reverse=True)


def _elite_tier_confidence(position, pos_rank_num):
    """High-confidence keep if this player is in the scarce elite tier at their position."""
    if position == 'TE' and pos_rank_num and pos_rank_num <= 5:
        return 'high', f'TE{pos_rank_num} - top 5 scarce, keep'
    if position == 'RB' and pos_rank_num and pos_rank_num <= 16:
        return 'high', f'RB{pos_rank_num} - top 16, premium keeper'
    if position == 'WR' and pos_rank_num and pos_rank_num <= 20:
        return 'high', f'WR{pos_rank_num} - top 20, keep for value'
    if position == 'QB' and pos_rank_num and pos_rank_num <= 15:
        return 'high', f'QB{pos_rank_num} - top 15, reasonable keeper'
    return None


def _best_available_confidence(keeper, position, rank, pos_rank_num, eligible_by_position):
    """Confidence based on being the best eligible option at a scarce position."""
    eligible_at_pos = eligible_by_position.get(position)
    if not eligible_at_pos:
        return None
    is_best_at_pos = normalize_name(eligible_at_pos[0]['name']) == normalize_name(keeper.get('playerName', ''))
    if not is_best_at_pos:
        return None

    next_best_rank = eligible_at_pos[1]['rank'] if len(eligible_at_pos) > 1 else None
    drop_off = (next_best_rank or 999) - (rank or 999) if next_best_rank and rank else 0
    pos_label = f'{position}{pos_rank_num or "?"}'

    if len(eligible_at_pos) <= 2:
        # Only 1-2 eligible = forced keeper
        reason = 'huge drop-off' if drop_off > 20 else f'only {len(eligible_at_pos)} eligible'
        return 'high', f'{pos_label} - forced keeper ({reason})'
    if drop_off > 25:
        # Big drop-off (25+ ranks) to next option = forced even with more alternatives
        return 'high', f'{pos_label} - forced keeper (major gap to next option)'
    if drop_off > 10:
        # Moderate gap = likely to keep
        return 'medium', f'{pos_label} - likely keeper (clear best option)'
    # Small gap or tied
    return 'medium', f'{pos_label} - best eligible at position'


def forecast_keeper_decisions(per_team, adp_map):
    """Forecast which keepers each team will likely keep based on position scarcity.

    Position scarcity is the key driver of keeper value - elite players at
    scarce positions (TE, RB) are worth keeping; everyone else is likely available
    in the draft at their positional tier.

    If per_team includes alternates, also shows whether teams are "forced" to keep a player
    (limited eligible keeper options) vs. "chosen" (good selection available).
    """
    from .adp_manager import normalize_player_name

    forecasts = []
    for team_entry in per_team:
        team_name = team_entry['team']
        chosen = team_entry.get('chosen', [])
        alternates = team_entry.get('alternates', [])

        # Group eligible keepers by position (chosen + alternates)
        eligible_by_position = {}
        for player in chosen + alternates:
            pos = player.get('position', 'UNK').upper()
            if pos not in eligible_by_position:
                eligible_by_position[pos] = []
            eligible_by_position[pos].append({
                'name': player.get('playerName'),
                'rank': player.get('ranking') or 999,
            })

        # Sort by rank within each position to find best/worst options
        for players in eligible_by_position.values():
            players.sort(key=lambda x: x['rank'])

        forecast_keepers = []
        for keeper in chosen:
            rank = keeper.get('ranking')
            position = keeper.get('position', '').upper()
            pos_rank = keeper.get('positionRank', '')

            # Extract positional rank (e.g., "TE2" -> 2)
            pos_rank_num = None
            if pos_rank:
                match = re.search(r'(\d+)', str(pos_rank))
                if match:
                    pos_rank_num = int(match.group(1))

            # Keeper decision: elite tier OR best available at position for this team
            confidence, reasoning = _elite_tier_confidence(position, pos_rank_num) or (None, None)
            if confidence is None:
                confidence, reasoning = _best_available_confidence(
                    keeper, position, rank, pos_rank_num, eligible_by_position,
                ) or ('low', 'Will be available in draft at this tier')

            # Look up ADP for this keeper
            normalized_name = normalize_player_name(keeper.get('playerName', ''))
            adp = adp_map.get(normalized_name) if adp_map else None

            forecast_keepers.append({
                'playerName': keeper.get('playerName'),
                'position': position,
                'rank': rank,
                'posRank': pos_rank,
                'confidence': confidence,
                'reasoning': reasoning,
                'adp': adp,
            })

        # Add top 2 alternates for comparison
        top_alternates = []
        for alt in alternates[:2]:
            alt_position = alt.get('position', '').upper()
            alt_rank = alt.get('ranking')
            alt_pos_rank = alt.get('positionRank', '')

            # Look up ADP
            normalized_alt_name = normalize_player_name(alt.get('playerName', ''))
            alt_adp = adp_map.get(normalized_alt_name) if adp_map else None

            top_alternates.append({
                'playerName': alt.get('playerName'),
                'position': alt_position,
                'rank': alt_rank,
                'posRank': alt_pos_rank,
                'adp': alt_adp,
                'reasoning': 'Next option considered',
            })

        forecasts.append({
            'team': team_name,
            'keepers': forecast_keepers,
            'alternates': top_alternates,
        })

    return forecasts


def keeper_board_state(
    league=None, *, keeper_count: Optional[int] = None, draft_years=None,
    include_file_prefs: bool = True, user_id: Optional[int] = None,
) -> dict:
    """Compute the full keeper-board state for either the default Yahoo league
    (league=None) or a resolved League. Used by keepers_board_view(),
    league_keepers(), and keeper_mark() so the AJAX response and the full-page
    render can never drift out of sync.

    user_id: applies that user's manual board offsets (app/board_service.py).
    None (anonymous) shows the unmodified data-derived board.

    Returns {'error': str} if rosters/rankings aren't available yet, otherwise
    {'repo', 'league_format', 'per_team', 'remaining_board', 'keeper_forecasts',
    'keeper_impact', 'include_marks', 'exclude_marks', 'error': None}.
    """
    if league is None:
        repo = get_repository()
        league_format = load_league_format()
        platform, platform_league_id = _default_league_platform_ids()
    else:
        repo = repository_for(league)
        league_format = league.format
        platform, platform_league_id = league.platform, league.platform_league_id

    league_rosters = repo.rosters()
    if not league_rosters:
        return {'error': (
            f'No saved league roster snapshot at {YAHOO_LEAGUE_ROSTERS_JSON}. '
            'Run `python3 -m app parse-rosters` first.'
        ) if league is None else 'No synced rosters yet -- sync the league first.'}

    rankings = repo.rankings()
    if not rankings:
        return {'error': (
            'No saved rankings. Run `python3 -m app.cli refresh-yahoo-rankings` or '
            '`import-rankings-csv` first.'
        ) if league is None else 'No synced rankings yet -- sync the league first.'}

    include_marks, exclude_marks = load_keeper_marks(platform, platform_league_id)
    resolved_keeper_count = keeper_count if keeper_count is not None else league_format.keeper_slots
    resolved_draft_years = draft_years if draft_years is not None else (repo.draft_years() if league is not None else None)
    per_team, remaining_board = league_keeper_board(
        league_rosters, rankings, league_format, keeper_count=resolved_keeper_count,
        keeper_prefs_override=include_marks, keeper_excludes_override=exclude_marks,
        draft_years=resolved_draft_years, include_file_prefs=include_file_prefs,
    )

    # Manual per-user offsets, then week-over-week movement -- both BEFORE the
    # top-N truncation, so a player who's been nudged up 60 spots can actually
    # reach the visible board.
    adjustments = (
        load_adjustments(user_id, platform, platform_league_id) if user_id else {}
    )
    remaining_board = apply_adjustments(remaining_board, adjustments)
    remaining_board = annotate_with_movement(remaining_board)
    remaining_board = remaining_board[:100]

    adp_map = load_adp_map()
    # Name-only lookup -- doesn't cover DEF (rosters use team nickname, e.g.
    # "Broncos"; rankings use "Denver Defense") the way roster_keeper_insight's
    # ranking already does via _find_ranking_for_player's team-code fallback.
    # Only use this to fill in a ranking that isn't already set, never to
    # clobber one insight already resolved correctly.
    rank_map = {r.get('playerName', '').lower(): r.get('ranking') for r in rankings}
    for team_entry in per_team:
        for chosen in team_entry.get('chosen', []):
            pos_rank = chosen.get('positionRank')
            if pos_rank:
                chosen['posRank'] = f"{chosen.get('position', 'UNK')}{pos_rank}"
            enrich_with_adp([chosen], adp_map)
            if chosen.get('ranking') is None:
                chosen['ranking'] = rank_map.get(chosen.get('playerName', '').lower())
        for alternate in team_entry.get('alternates', []):
            enrich_with_adp([alternate], adp_map)
            if alternate.get('ranking') is None:
                alternate['ranking'] = rank_map.get(alternate.get('playerName', '').lower())
    for row in remaining_board:
        enrich_with_adp([row], adp_map)

    keeper_forecasts = forecast_keeper_decisions(per_team, adp_map)
    keeper_impact = calculate_keeper_impact(keeper_forecasts, league_format=league_format)

    # Fixed candidate pool per team for the clickable card UI: EVERY keeper-
    # eligible player, drawn from eligiblePool (unaffected by include/exclude
    # state -- see league_keeper_board) so the same cards stay in place and in
    # the same order no matter which ones are currently toggled kept. Shows
    # the whole roster, not just a top-N slice -- a manager might have a real
    # reason to keep someone the ranking-driven algorithm wouldn't surface as
    # a top pick, and that shouldn't be impossible to select just because
    # they're not near the top of the board. Only `chosen` membership (which
    # DOES reflect excludes) decides each card's kept state.
    for team_entry in per_team:
        chosen_ids = {c.get('playerId') for c in team_entry.get('chosen', [])}
        pool = team_entry.get('eligiblePool', [])
        for player in pool:
            enrich_with_adp([player], adp_map)
            if player.get('ranking') is None:
                player['ranking'] = rank_map.get(player.get('playerName', '').lower())
        team_entry['candidates'] = [
            {
                **player,
                'kept': player.get('playerId') in chosen_ids or player.get('keeperLocked') is True,
                'locked': player.get('keeperLocked') is True,
            }
            for player in pool
        ]

    return {
        'repo': repo,
        'league_format': league_format,
        'keeper_count': resolved_keeper_count,
        'per_team': per_team,
        'remaining_board': remaining_board,
        'keeper_forecasts': keeper_forecasts,
        'keeper_impact': keeper_impact,
        'include_marks': include_marks,
        'exclude_marks': exclude_marks,
        'error': None,
    }


def _next_draft_season(draft_years) -> int:
    """The upcoming draft year: one past the latest season this league has
    draft_history/draft-snapshot data for. Mirrors cli.py's _next_draft_season
    but works off an already-loaded per-league draft_years dict instead of
    the global Yahoo loader, so it's correct for any registered league."""
    return max(draft_years.keys()) + 1 if draft_years else 2026


def log_team_keeper_forecast(state: dict, team: str, platform: str, platform_league_id: str) -> None:
    """Log the outcome-log 'agent Learn pillar' forecast (app/outcome_log.py)
    for one team's current chosen keepers, after a keeper_mark() toggle.

    Only the touched team is (re-)logged, not the whole board -- keeps this
    cheap per click. log_outcome() upserts by decision_id while an entry is
    still pending, so re-clicking the same team's cards just updates the
    forecast in place rather than accumulating duplicates. Runs for every
    registered league (not just Yahoo) since keeper_board_state() already
    resolves per-league draft_years."""
    team_entry = next((t for t in state['per_team'] if t['team'] == team), None)
    if team_entry is None:
        return

    # state['repo'] is already the correct repository for whichever league
    # this call is for (default Yahoo or a resolved League) -- reuse it
    # rather than re-resolving from platform/platform_league_id.
    season = _next_draft_season(state['repo'].draft_years())

    log_batch = load_outcomes()
    for i, keeper in enumerate(team_entry.get('chosen', []), 1):
        log_outcome(
            'keeper_forecast', season, keeper['playerName'],
            forecast={'keeper_status': f'Keeper {i}', 'ranking': keeper.get('ranking')},
            method_version=KEEPER_FORECAST_METHOD_VERSION,
            team=team, outcomes=log_batch,
            platform=platform, platform_league_id=platform_league_id,
        )
    if team_entry.get('chosen'):
        save_outcomes(log_batch)
