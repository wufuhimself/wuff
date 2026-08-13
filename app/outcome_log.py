"""Agent outcome log — forecast-vs-actual tracking for the Learn pillar.

Every scored recommendation the app makes (keeper forecasts, QB draft-slot
adjustments, ...) gets logged here at forecast time via log_outcome(). Once the
real outcome is known (a draft happens, a season ends), resolve_outcomes()
fills in the actual result and computes a delta, so forecast accuracy can be
compared across method_version changes over time instead of just trusted on
faith.

Per-league (2026-08-11): every entry is scoped by (platform, platform_league_id),
same convention as KeeperMark (app/models.py) — a single shared log file, not
one file per league, since cross-league forecast-accuracy comparison is itself
a feature the roadmap wants. Callers that omit platform/platform_league_id
default to the Yahoo frank-gore league (platform='yahoo', id='9410') for
backward compatibility with the original single-league entries and CLI call
sites (app/cli.py's apply-qb-adjustment / keepers-board-export) — those never
passed a league explicitly and shouldn't change decision_ids for pre-existing
pending/resolved entries.

Storage: single append-only array at data/processed/outcome_log.json, keyed
by decision_id (now including platform+platform_league_id) so a later
forecast for the same (league, decision_type, season, entity) overwrites the
previous pending one rather than piling up duplicates. Resolved entries are
left alone — they're historical record.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .draft_history import keeper_slot_picks, live_draft_picks, load_draft_years
from .league_registry import load_leagues
from .paths import PROCESSED_DIR, ensure_parent_dir
from .repository import get_repository, repository_for

OUTCOME_LOG_FILE = PROCESSED_DIR / 'outcome_log.json'

# Default league for callers that don't specify one -- keeps existing
# decision_ids and CLI call sites (app/cli.py) unchanged.
DEFAULT_PLATFORM = 'yahoo'
DEFAULT_PLATFORM_LEAGUE_ID = '9410'


def _normalize(value: str) -> str:
    return ' '.join(str(value).strip().lower().split())


def _decision_id(
    decision_type: str, season: int, entity: str, team: Optional[str] = None,
    platform: str = DEFAULT_PLATFORM, platform_league_id: str = DEFAULT_PLATFORM_LEAGUE_ID,
) -> str:
    # Yahoo/default league keeps the original (unscoped) id format so
    # existing entries in outcome_log.json don't get orphaned by this change.
    league_prefix = None if (platform, platform_league_id) == (DEFAULT_PLATFORM, DEFAULT_PLATFORM_LEAGUE_ID) \
        else f'{platform}-{platform_league_id}'
    parts = [league_prefix, decision_type, str(season), _normalize(team) if team else None, _normalize(entity)]
    return '_'.join(p.replace(' ', '-') for p in parts if p)


def load_outcomes(path=None) -> List[Dict[str, Any]]:
    path = path or OUTCOME_LOG_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_outcomes(outcomes: List[Dict[str, Any]], path=None) -> None:
    path = path or OUTCOME_LOG_FILE
    ensure_parent_dir(path)
    path.write_text(json.dumps(outcomes, indent=2))


def log_outcome(
    decision_type: str,
    season: int,
    entity: str,
    forecast: Dict[str, Any],
    method_version: str,
    team: Optional[str] = None,
    outcomes: Optional[List[Dict[str, Any]]] = None,
    platform: str = DEFAULT_PLATFORM,
    platform_league_id: str = DEFAULT_PLATFORM_LEAGUE_ID,
) -> Dict[str, Any]:
    """Record a forecast. Upserts by decision_id if that entry is still pending.

    Pass an existing `outcomes` list to batch multiple log_outcome() calls
    before a single save_outcomes() — avoids re-reading/re-writing the file
    once per player when logging e.g. a full keeper board.

    platform/platform_league_id: scope this forecast to a specific league
    (see module docstring). Omit for the Yahoo frank-gore league (default,
    matches all pre-2026-08-11 entries).
    """
    owns_list = outcomes is None
    outcomes = load_outcomes() if owns_list else outcomes

    decision_id = _decision_id(decision_type, season, entity, team, platform, platform_league_id)
    entry = {
        'decision_id': decision_id,
        'decision_type': decision_type,
        'platform': platform,
        'platform_league_id': platform_league_id,
        'season': season,
        'entity': entity,
        'team': team,
        'forecast': forecast,
        'forecast_method_version': method_version,
        'forecasted_at': datetime.now().isoformat(timespec='seconds'),
        'actual': None,
        'resolved_at': None,
        'delta': None,
        'status': 'pending',
    }

    for i, existing in enumerate(outcomes):
        if existing['decision_id'] == decision_id:
            if existing['status'] == 'pending':
                outcomes[i] = entry
            return outcomes[i]
    outcomes.append(entry)

    if owns_list:
        save_outcomes(outcomes)
    return entry


def _resolve_keeper_forecasts(outcomes: List[Dict[str, Any]], years_data) -> int:
    resolved = 0
    now = datetime.now().isoformat(timespec='seconds')
    for entry in outcomes:
        if entry['status'] != 'pending' or entry['decision_type'] != 'keeper_forecast':
            continue
        season = entry['season']
        if season not in years_data:
            continue
        kept_names = {_normalize(p.get('playerName', '')) for p in keeper_slot_picks(season, years_data)}
        was_kept = _normalize(entry['entity']) in kept_names
        predicted_kept = str(entry['forecast'].get('keeper_status', '')).startswith('Keeper')
        entry['actual'] = {'kept': was_kept}
        entry['delta'] = 0 if predicted_kept == was_kept else 1
        entry['resolved_at'] = now
        entry['status'] = 'resolved'
        resolved += 1
    return resolved


def _resolve_qb_adjustments(outcomes: List[Dict[str, Any]], years_data, teams: int) -> int:
    resolved = 0
    now = datetime.now().isoformat(timespec='seconds')
    for entry in outcomes:
        if entry['status'] != 'pending' or entry['decision_type'] != 'qb_adjustment':
            continue
        season = entry['season']
        if season not in years_data:
            continue
        picks = live_draft_picks(season, years_data)
        target_name = _normalize(entry['entity'])
        actual_pick = None
        for pick in picks:
            if _normalize(str(pick.get('playerName', ''))) == target_name:
                actual_pick = (pick.get('round', 0) - 1) * teams + pick.get('pick', 0)
                break
        if actual_pick is None:
            continue
        entry['actual'] = {'overall_pick': actual_pick}
        entry['delta'] = actual_pick - entry['forecast'].get('target_pick', actual_pick)
        entry['resolved_at'] = now
        entry['status'] = 'resolved'
        resolved += 1
    return resolved


def accuracy_report(
    platform: Optional[str] = None, platform_league_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate resolved-forecast accuracy, grouped by league + decision_type
    + forecast_method_version -- this is the "read the log back" step the
    README's What's Next calls out (log_outcome/resolve_outcomes only write
    and match; nothing previously read the result). One row per group, with
    both `resolved` and `pending` counts, so a group with i.e. 0 resolved
    shows up as "no signal yet" rather than being silently absent -- with no
    season having drafted yet as of 2026-08-11, that's every group.

    Accuracy metric depends on decision_type, since `delta`'s meaning does:
    - keeper_forecast: delta is 0 (hit) or 1 (miss) -- `hit_rate` reported.
    - qb_adjustment: delta is a signed pick-count miss -- `mean_delta` (bias
      direction: positive means the real pick came later than forecast) and
      `mean_abs_delta` (typical miss size, direction-blind) both reported.

    Omit platform/platform_league_id for a report across every league in the
    shared log (this file is intentionally one log for all leagues -- see
    module docstring -- so cross-league method comparison is a report filter,
    not a separate file). Pass both to scope to one league.

    Deliberately stops here: it reports accuracy, it does not feed it back
    into any scoring weight. That's the next step after this one, and it
    needs real resolved volume to tune against safely -- not zero.
    """
    outcomes = load_outcomes()
    if platform is not None:
        outcomes = [
            o for o in outcomes
            if (o.get('platform') or DEFAULT_PLATFORM) == platform
            and (o.get('platform_league_id') or DEFAULT_PLATFORM_LEAGUE_ID) == platform_league_id
        ]

    groups: Dict[tuple, Dict[str, Any]] = {}
    for entry in outcomes:
        key = (
            entry.get('platform') or DEFAULT_PLATFORM,
            entry.get('platform_league_id') or DEFAULT_PLATFORM_LEAGUE_ID,
            entry['decision_type'],
            entry['forecast_method_version'],
        )
        bucket = groups.setdefault(key, {'resolved': 0, 'pending': 0, 'deltas': []})
        if entry['status'] == 'resolved':
            bucket['resolved'] += 1
            bucket['deltas'].append(entry['delta'])
        else:
            bucket['pending'] += 1

    report = []
    for (plat, league_id, decision_type, method_version), bucket in sorted(groups.items()):
        row = {
            'platform': plat,
            'platform_league_id': league_id,
            'decision_type': decision_type,
            'forecast_method_version': method_version,
            'resolved': bucket['resolved'],
            'pending': bucket['pending'],
        }
        deltas = bucket['deltas']
        if deltas:
            if decision_type == 'keeper_forecast':
                row['hit_rate'] = round(1 - sum(deltas) / len(deltas), 3)
            elif decision_type == 'qb_adjustment':
                row['mean_delta'] = round(sum(deltas) / len(deltas), 1)
                row['mean_abs_delta'] = round(sum(abs(d) for d in deltas) / len(deltas), 1)
        report.append(row)
    return report


def resolve_outcomes(teams: int = 12) -> Dict[str, int]:
    """Attempt to resolve every pending entry against its own league's current
    draft_history data.

    Groups pending entries by (platform, platform_league_id) -- legacy
    entries with no platform field are treated as the default Yahoo league --
    and resolves each group against that league's own draft_years via
    app/repository.py, so a Sleeper/ESPN league's forecasts resolve against
    its own synced draft, not Yahoo's draft_history files. `teams` is the
    fallback team count for legacy/undetermined leagues only; each resolved
    league prefers its own LeagueFormat.teams for the pick-number math.

    Entries whose season's draft hasn't happened yet (no matching draft data
    for that league/season) are left pending, not errored.
    """
    outcomes = load_outcomes()

    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for entry in outcomes:
        if entry['status'] != 'pending':
            continue
        key = (entry.get('platform') or DEFAULT_PLATFORM, entry.get('platform_league_id') or DEFAULT_PLATFORM_LEAGUE_ID)
        groups.setdefault(key, []).append(entry)

    leagues_by_key = {(l.platform, l.platform_league_id): l for l in load_leagues().values()}

    resolved = 0
    for (platform, platform_league_id), group_entries in groups.items():
        league = leagues_by_key.get((platform, platform_league_id))
        if league is not None:
            years_data = repository_for(league).draft_years()
            league_teams = league.format.teams
        else:
            # Unregistered/legacy league (or the default Yahoo league, which
            # historically read draft_history directly rather than through
            # the repository) -- fall back to the default league's repository
            # + passed teams. Not load_draft_years(): that reads the
            # gitignored JSON, which is empty on a deployed container now
            # that draft history lives in the database.
            try:
                years_data = get_repository().draft_years()
            except Exception:  # pylint: disable=broad-except
                years_data = load_draft_years()
            league_teams = teams
        resolved += _resolve_keeper_forecasts(group_entries, years_data)
        resolved += _resolve_qb_adjustments(group_entries, years_data, league_teams)

    save_outcomes(outcomes)

    pending = sum(1 for o in outcomes if o['status'] == 'pending')
    return {'resolved_this_run': resolved, 'still_pending': pending, 'total': len(outcomes)}
