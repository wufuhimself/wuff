"""Agent outcome log — forecast-vs-actual tracking for the Learn pillar.

Every scored recommendation the app makes (keeper forecasts, QB draft-slot
adjustments, ...) gets logged here at forecast time via log_outcome(). Once the
real outcome is known (a draft happens, a season ends), resolve_outcomes()
fills in the actual result and computes a delta, so forecast accuracy can be
compared across method_version changes over time instead of just trusted on
faith.

Storage: single append-only array at data/processed/outcome_log.json, keyed
by decision_id so a later forecast for the same (decision_type, season,
entity) overwrites the previous pending one rather than piling up duplicates.
Resolved entries are left alone — they're historical record.
"""
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from .draft_history import keeper_slot_picks, live_draft_picks, load_draft_years
from .paths import PROCESSED_DIR, ensure_parent_dir

OUTCOME_LOG_FILE = PROCESSED_DIR / 'outcome_log.json'


def _normalize(value: str) -> str:
    return ' '.join(str(value).strip().lower().split())


def _decision_id(decision_type: str, season: int, entity: str, team: Optional[str] = None) -> str:
    parts = [decision_type, str(season), _normalize(team) if team else None, _normalize(entity)]
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
) -> Dict[str, Any]:
    """Record a forecast. Upserts by decision_id if that entry is still pending.

    Pass an existing `outcomes` list to batch multiple log_outcome() calls
    before a single save_outcomes() — avoids re-reading/re-writing the file
    once per player when logging e.g. a full keeper board.
    """
    owns_list = outcomes is None
    outcomes = load_outcomes() if owns_list else outcomes

    decision_id = _decision_id(decision_type, season, entity, team)
    entry = {
        'decision_id': decision_id,
        'decision_type': decision_type,
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


def resolve_outcomes(teams: int = 12) -> Dict[str, int]:
    """Attempt to resolve every pending entry against current draft_history data.

    Entries whose season's draft hasn't happened yet (no matching
    data/raw/draft_history/{season}.json) are left pending, not errored.
    """
    outcomes = load_outcomes()
    years_data = load_draft_years()

    resolved = 0
    resolved += _resolve_keeper_forecasts(outcomes, years_data)
    resolved += _resolve_qb_adjustments(outcomes, years_data, teams)

    save_outcomes(outcomes)

    pending = sum(1 for o in outcomes if o['status'] == 'pending')
    return {'resolved_this_run': resolved, 'still_pending': pending, 'total': len(outcomes)}
