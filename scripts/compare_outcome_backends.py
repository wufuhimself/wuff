#!/usr/bin/env python3
"""Assert the DB-backed outcome log returns exactly what the JSON one does.

The gate on the outcome-log JSON->DB migration (2026-08-20). Same discipline
as scripts/compare_yahoo_backends.py: diff FULL output of every read, for the
real local log, rather than checking "did it run" -- every bug this codebase
has logged from a storage swap was silent.

Unlike the Yahoo gate, this also exercises the WRITE path. The Yahoo data is
imported once and then read; the outcome log is mutated continuously
(log_outcome upserts pending entries, appends superseded forecasts, and
resolve_outcomes rewrites resolved ones), and those mutations are where the
two backends can actually diverge -- the JSON array carries insertion order
implicitly, the DB has to reproduce it from a column.

Runs against a THROWAWAY SQLite file, not the real database: it wipes and
reloads both tables, so pointing it at production would destroy the very data
this migration exists to preserve. It refuses to run if DATABASE_URL is
already set for that reason.

    python3 scripts/compare_outcome_backends.py

Exits non-zero on any difference.
"""
import copy
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if os.environ.get('DATABASE_URL'):
    print('DATABASE_URL is set. This script wipes both outcome tables -- refusing to run')
    print('against a real database. Unset it and re-run (it uses a throwaway SQLite file).')
    sys.exit(2)

# A throwaway SQLite database, set BEFORE app.db is imported (it reads the env
# var at import time).
_TMP_DB = Path(tempfile.mkdtemp(prefix='wuff-outcome-gate-')) / 'gate.db'
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB}'

# pylint: disable=wrong-import-position
from app import outcome_log, outcome_store  # noqa: E402
from app.db import init_db  # noqa: E402


def _describe(value) -> str:
    text = repr(value)
    return text if len(text) <= 400 else text[:400] + f'... ({len(text)} chars)'


def _compare(label, expected, actual, failures) -> None:
    if expected == actual:
        print(f'  ok   {label}')
        return
    failures.append(label)
    print(f'  FAIL {label}')
    print(f'       json: {_describe(expected)}')
    print(f'       db:   {_describe(actual)}')


def main() -> int:
    failures = []
    init_db()

    # --- Reference: the real local JSON log, read through the file backend. ---
    # normalize_entry fills in the per-league fields pre-2026-08-11 entries
    # were written without -- applied to the reference too, so this stays a
    # real diff of storage rather than re-testing that normalization ran.
    # Its correctness (those 4 entries keep their exact decision_id) is
    # asserted separately below.
    json_outcomes = [outcome_store.normalize_entry(e)
                     for e in outcome_log.load_outcomes(path=outcome_log.OUTCOME_LOG_FILE)]
    json_history = outcome_log.load_outcome_history(path=outcome_log.OUTCOME_LOG_HISTORY_FILE)

    print('legacy entries (no platform/platform_league_id) keep their decision_id')
    raw = outcome_log.load_outcomes(path=outcome_log.OUTCOME_LOG_FILE)
    legacy = [e for e in raw if 'platform' not in e]
    for entry in legacy:
        rebuilt = outcome_log._decision_id(  # pylint: disable=protected-access
            entry['decision_type'], entry['season'], entry['entity'], entry['team'],
            outcome_log.DEFAULT_PLATFORM, outcome_log.DEFAULT_PLATFORM_LEAGUE_ID)
        _compare(f'legacy id {entry["decision_id"]!r}', entry['decision_id'], rebuilt, failures)
    if not legacy:
        print('  (none in this log)')
    print()
    print(f'Reference: {len(json_outcomes)} outcome(s), {len(json_history)} history entr(ies) '
          f'from {outcome_log.OUTCOME_LOG_FILE.name}')
    if not json_outcomes:
        print('No local outcome log to compare against -- run the app or the CLI first.')
        return 2
    print()

    # --- Load it into the DB, read it back. ---
    print('round-trip: load_outcomes / load_outcome_history')
    outcome_store.replace_all(json_outcomes, json_history)
    _compare('load_outcomes()', json_outcomes, outcome_store.load_outcomes(), failures)
    _compare('load_outcome_history()', json_history, outcome_store.load_outcome_history(), failures)

    # Key ORDER too, not just equality -- dict == ignores it, but consumers
    # re-serialize these entries and a full-output diff would show the churn.
    print()
    print('key order preserved')
    db_outcomes = outcome_store.load_outcomes()
    order_ok = all(list(a) == list(b) for a, b in zip(json_outcomes, db_outcomes))
    _compare('entry key order', True, order_ok, failures)

    # --- Reads derived from the log agree across backends. ---
    print()
    print('derived reads')
    ids_with_history = sorted({e['decision_id'] for e in json_history})
    for decision_id in ids_with_history[:5]:
        expected = [e for e in json_history if e['decision_id'] == decision_id]
        _compare(f'forecast_history({decision_id!r})', expected,
                 outcome_log.forecast_history(decision_id), failures)
    _compare('accuracy_report()',
             _accuracy_from(json_outcomes),
             outcome_log.accuracy_report(), failures)

    # --- Write path: an upsert that CHANGES a pending forecast must update the
    # entry in place, keep its position in the list, and append one history row.
    print()
    print('write path: upsert of a pending entry')
    pending = next((e for e in json_outcomes if e['status'] == 'pending'), None)
    if pending is None:
        print('  skip  no pending entry in the reference log')
    else:
        index = json_outcomes.index(pending)
        changed = dict(pending['forecast'])
        changed['_gate_marker'] = 'changed'

        # File backend, on a copy of the real list, as the reference.
        file_outcomes = copy.deepcopy(json_outcomes)
        outcome_log.log_outcome(
            decision_type=pending['decision_type'], season=pending['season'],
            entity=pending['entity'], forecast=changed,
            method_version=pending['forecast_method_version'], team=pending['team'],
            outcomes=file_outcomes, platform=pending['platform'],
            platform_league_id=pending['platform_league_id'],
        )

        # DB backend, from the same starting state.
        outcome_store.replace_all(json_outcomes, json_history)
        outcome_log.log_outcome(
            decision_type=pending['decision_type'], season=pending['season'],
            entity=pending['entity'], forecast=changed,
            method_version=pending['forecast_method_version'], team=pending['team'],
            platform=pending['platform'], platform_league_id=pending['platform_league_id'],
        )
        db_after = outcome_store.load_outcomes()

        # forecasted_at is a timestamp written at call time; the two calls can
        # land in different seconds, so compare everything else.
        def _blank_ts(entries):
            return [{**e, 'forecasted_at': None} for e in entries]

        _compare('upsert: full list', _blank_ts(file_outcomes), _blank_ts(db_after), failures)
        _compare('upsert: list length unchanged', len(json_outcomes), len(db_after), failures)
        _compare('upsert: entry stayed at its index', pending['decision_id'],
                 db_after[index]['decision_id'], failures)
        _compare('upsert: forecast updated', changed, db_after[index]['forecast'], failures)
        _compare('upsert: one history row appended', len(json_history) + 1,
                 len(outcome_store.load_outcome_history()), failures)

        # And an upsert with an UNCHANGED forecast must append nothing.
        outcome_log.log_outcome(
            decision_type=pending['decision_type'], season=pending['season'],
            entity=pending['entity'], forecast=changed,
            method_version=pending['forecast_method_version'], team=pending['team'],
            platform=pending['platform'], platform_league_id=pending['platform_league_id'],
        )
        _compare('re-log of an identical forecast appends no history',
                 len(json_history) + 1, len(outcome_store.load_outcome_history()), failures)

    # --- A brand-new entry appends at the end, like the JSON array did. ---
    print()
    print('write path: brand-new entry')
    outcome_store.replace_all(json_outcomes, json_history)
    outcome_log.log_outcome(
        decision_type='keeper_forecast', season=1999, entity='Gate Test Player',
        forecast={'kept': True}, method_version='gate_v1', team='Gate Test Team',
        platform='gate', platform_league_id='0',
    )
    appended = outcome_store.load_outcomes()
    _compare('new entry appended at the end', len(json_outcomes) + 1, len(appended), failures)
    _compare('new entry is last', 'Gate Test Player', appended[-1]['entity'], failures)
    _compare('existing entries unchanged', json_outcomes, appended[:-1], failures)

    # --- resolve_outcomes() mutates entries in place then re-saves the whole
    # list, which the DB backend has to turn into updates rather than inserts.
    print()
    print('write path: resolve_outcomes()')
    outcome_store.replace_all(json_outcomes, json_history)
    before = outcome_store.load_outcomes()
    summary = outcome_log.resolve_outcomes()
    after = outcome_store.load_outcomes()
    _compare('resolve: no entries created or dropped', len(before), len(after), failures)
    _compare('resolve: order preserved',
             [e['decision_id'] for e in before], [e['decision_id'] for e in after], failures)
    # Nothing has drafted this cycle, so every entry should still be pending --
    # a resolution here would mean the resolver found data it shouldn't have.
    _compare('resolve: summary reports what the rows show',
             summary['still_pending'], sum(1 for e in after if e['status'] == 'pending'), failures)
    still_pending = [e for e in after if e['status'] == 'pending']
    if len(still_pending) == len(after):
        _compare('resolve: unresolved entries untouched', before, after, failures)
    else:
        print(f'  note  {len(after) - len(still_pending)} entr(ies) resolved '
              f'(a draft has happened) -- in-place update path exercised')

    print()
    if failures:
        print(f'{len(failures)} difference(s): ' + ', '.join(failures))
        return 1
    print('Both backends agree.')
    return 0


def _accuracy_from(outcomes):
    """accuracy_report() over an explicit list, for the JSON-side reference.

    accuracy_report() reads the active backend, so the reference has to be
    computed by pointing that backend at the file -- done by monkeypatching
    load_outcomes for the duration rather than duplicating the aggregation
    here, which would test this script's arithmetic instead of the backend.
    """
    original = outcome_log.load_outcomes
    outcome_log.load_outcomes = lambda path=None: outcomes  # type: ignore[assignment]
    try:
        return outcome_log.accuracy_report()
    finally:
        outcome_log.load_outcomes = original  # type: ignore[assignment]


if __name__ == '__main__':
    sys.exit(main())
