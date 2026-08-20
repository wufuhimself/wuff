"""Read/write the outcome log's DB rows -- the durable backend behind
app/outcome_log.py's load/save functions. See outcome_models.py for why this
exists (short version: data/processed/ never survives a Railway redeploy).

Mirrors the list-of-dicts shape outcome_log.py has always passed around, so
its four storage functions swap their bodies and every caller elsewhere
(keeper_service, cli, agent_reasoning, scripts/langgraph_spike) keeps working
against the same dicts. The entry dict's key order is preserved to match the
JSON file's, so a full-output diff of any consumer stays byte-identical.

This is the only backend now, local dev included -- the JSON files are
migration input (`python3 -m app migrate-outcome-log`), not a second live
store. Deliberately not a DB-in-production/files-locally split: that leaves
two write paths to keep in agreement, and every other piece of user state
(accounts, keeper marks, board adjustments) already lives in the local
SQLite database anyway.
"""
import json
from typing import Any, Dict, List, Optional

from .db import SessionLocal
from .outcome_models import OutcomeEntry, OutcomeHistoryEntry

# The key order outcome_log.py's log_outcome() builds its entry dict in. Kept
# explicit so rows read back from the DB serialize identically to the ones the
# JSON file held -- consumers compare and re-save whole dicts.
_ENTRY_KEYS = (
    'decision_id', 'decision_type', 'platform', 'platform_league_id', 'season',
    'entity', 'team', 'forecast', 'forecast_method_version', 'forecasted_at',
    'actual', 'resolved_at', 'delta', 'status',
)


def _dumps(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads(value: Optional[str]) -> Any:
    return None if value is None else json.loads(value)


def _row_to_dict(row: OutcomeEntry) -> Dict[str, Any]:
    return {
        'decision_id': row.decision_id,
        'decision_type': row.decision_type,
        'platform': row.platform,
        'platform_league_id': row.platform_league_id,
        'season': row.season,
        'entity': row.entity,
        'team': row.team,
        'forecast': _loads(row.forecast_json),
        'forecast_method_version': row.forecast_method_version,
        'forecasted_at': row.forecasted_at,
        'actual': _loads(row.actual_json),
        'resolved_at': row.resolved_at,
        'delta': _loads(row.delta_json),
        'status': row.status,
    }


# The league every entry written before 2026-08-11 implicitly belonged to.
# Duplicated from outcome_log.DEFAULT_PLATFORM rather than imported, because
# importing it here would be circular (outcome_log imports this module).
_LEGACY_PLATFORM = 'yahoo'
_LEGACY_PLATFORM_LEAGUE_ID = '9410'


def normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in the per-league fields pre-2026-08-11 entries were written
    without. outcome_log.py already treats those entries as the default Yahoo
    league everywhere -- _decision_id() gives that league the un-prefixed id
    format precisely so they keep their original ids -- so this makes an
    existing implicit default explicit rather than reassigning anything.
    Verified against the real log: all 4 legacy entries keep their exact
    decision_id.

    Applied on the way into the DB, not left to nullable columns: the missing
    fields are a JSON-file artifact, and reproducing them would mean carrying
    a per-row key-order difference in the schema forever.
    """
    if 'platform' in entry and 'platform_league_id' in entry:
        return entry
    filled = dict(entry)
    filled.setdefault('platform', _LEGACY_PLATFORM)
    filled.setdefault('platform_league_id', _LEGACY_PLATFORM_LEAGUE_ID)
    # Rebuilt in _ENTRY_KEYS order so a migrated legacy entry is
    # indistinguishable from one logged today.
    return {key: filled[key] for key in _ENTRY_KEYS if key in filled}


def _apply(row: OutcomeEntry, entry: Dict[str, Any]) -> None:
    entry = normalize_entry(entry)
    row.decision_type = entry['decision_type']
    row.platform = entry['platform']
    row.platform_league_id = entry['platform_league_id']
    row.season = entry['season']
    row.entity = entry['entity']
    row.team = entry.get('team')
    row.forecast_json = _dumps(entry['forecast'])
    row.forecast_method_version = entry['forecast_method_version']
    row.forecasted_at = entry['forecasted_at']
    row.actual_json = _dumps(entry.get('actual'))
    row.resolved_at = entry.get('resolved_at')
    row.delta_json = _dumps(entry.get('delta'))
    row.status = entry.get('status', 'pending')


def load_outcomes() -> List[Dict[str, Any]]:
    """Every logged forecast, in insertion order (matching the JSON array)."""
    with SessionLocal() as session:
        rows = session.query(OutcomeEntry).order_by(OutcomeEntry.seq).all()
        return [_row_to_dict(row) for row in rows]


def save_outcomes(outcomes: List[Dict[str, Any]]) -> None:
    """Persist the whole list, upserting by decision_id.

    outcome_log.py's callers hand back a list they loaded, mutated in place
    and appended to -- so this writes every entry rather than diffing. Rows
    whose decision_id is absent from the list are deleted, which keeps the DB
    a faithful mirror of what the JSON file's whole-array rewrite produced;
    nothing in the codebase removes entries today, but a silent divergence
    between the two backends is exactly the failure this migration exists to
    avoid.
    """
    with SessionLocal() as session:
        existing = {row.decision_id: row for row in session.query(OutcomeEntry).all()}
        next_seq = max((row.seq for row in existing.values()), default=0) + 1
        seen = set()
        for entry in outcomes:
            decision_id = entry['decision_id']
            seen.add(decision_id)
            row = existing.get(decision_id)
            if row is None:
                row = OutcomeEntry(decision_id=decision_id, seq=next_seq)
                next_seq += 1
                session.add(row)
            _apply(row, entry)
        for decision_id, row in existing.items():
            if decision_id not in seen:
                session.delete(row)
        session.commit()


def load_outcome_history() -> List[Dict[str, Any]]:
    """Every superseded forecast, oldest first."""
    with SessionLocal() as session:
        rows = session.query(OutcomeHistoryEntry).order_by(OutcomeHistoryEntry.id).all()
        return [
            {
                'decision_id': row.decision_id,
                'superseded_at': row.superseded_at,
                'forecast': _loads(row.forecast_json),
                'forecast_method_version': row.forecast_method_version,
            }
            for row in rows
        ]


def append_outcome_history(entry: Dict[str, Any]) -> None:
    with SessionLocal() as session:
        session.add(OutcomeHistoryEntry(
            decision_id=entry['decision_id'],
            superseded_at=entry.get('superseded_at'),
            forecast_json=_dumps(entry['forecast']),
            forecast_method_version=entry.get('forecast_method_version'),
        ))
        session.commit()


def replace_all(outcomes: List[Dict[str, Any]], history: List[Dict[str, Any]]) -> None:
    """Wipe both tables and load these lists -- the one-time migration path
    (`python3 -m app migrate-outcome-log`). Separate from save_outcomes()
    because it also seeds history, and because rewriting seq from scratch is
    correct exactly once (when importing the JSON file's own order) and wrong
    every other time."""
    with SessionLocal() as session:
        session.query(OutcomeEntry).delete()
        session.query(OutcomeHistoryEntry).delete()
        for seq, entry in enumerate(outcomes, start=1):
            row = OutcomeEntry(decision_id=entry['decision_id'], seq=seq)
            _apply(row, entry)
            session.add(row)
        for entry in history:
            session.add(OutcomeHistoryEntry(
                decision_id=entry['decision_id'],
                superseded_at=entry.get('superseded_at'),
                forecast_json=_dumps(entry['forecast']),
                forecast_method_version=entry.get('forecast_method_version'),
            ))
        session.commit()
