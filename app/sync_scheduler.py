"""Background Sleeper sync (Phase 1): APScheduler in-process, no Redis/queue infra.

Two entry points:
- ensure_scheduler_started(): idempotent; adds a periodic sweep of every
  known Sleeper league (DB-imported ones plus the local
  sleeper_leagues.json set). The web app calls this lazily on first request.
- queue_league_sync(id): one-off background sync (onboarding import, the
  "Sync now" button). Falls back to a synchronous inline sync when the
  scheduler is disabled (WUFF_DISABLE_SCHEDULER=1 — tests, one-shot CLI).

Every attempt is recorded as a SyncRun row (status running -> ok/error), so
the UI can show "last synced X, status Y" without parsing snapshot files.
API pacing is enforced globally in sleeper_client, not here.
"""
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from apscheduler.schedulers.background import BackgroundScheduler

from .db import SessionLocal
from .models import DbLeague, SyncRun
from .paths import SLEEPER_PLAYERS_CACHE_FILE
from .sleeper_manager import (
    load_players_cache,
    load_sleeper_leagues_config,
    refresh_players_cache,
    sync_league,
)

SYNC_INTERVAL_MINUTES = int(os.environ.get('SLEEPER_SYNC_INTERVAL_MINUTES', '360'))
PLAYERS_CACHE_MAX_AGE_HOURS = int(os.environ.get('SLEEPER_PLAYERS_CACHE_MAX_AGE_HOURS', '168'))

_scheduler: Optional[BackgroundScheduler] = None  # pylint: disable=invalid-name
_scheduler_lock = threading.Lock()


def _scheduler_disabled() -> bool:
    return os.environ.get('WUFF_DISABLE_SCHEDULER') == '1'


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fresh_players_cache() -> dict:
    """Shared player-id lookup, refreshed at most every PLAYERS_CACHE_MAX_AGE_HOURS
    (it's a ~5MB fetch — never per-sync)."""
    cache = load_players_cache()
    if cache and SLEEPER_PLAYERS_CACHE_FILE.exists():
        age_hours = (
            datetime.now().timestamp() - SLEEPER_PLAYERS_CACHE_FILE.stat().st_mtime
        ) / 3600
        if age_hours < PLAYERS_CACHE_MAX_AGE_HOURS:
            return cache
    refresh_players_cache()
    return load_players_cache()


def sync_one_league(platform_league_id: str) -> str:
    """Sync one league's snapshots, recording a SyncRun. Returns final status."""
    with SessionLocal() as session:
        run = SyncRun(platform='sleeper', platform_league_id=platform_league_id, status='running')
        session.add(run)
        session.commit()
        run_id = run.id

    try:
        summary = sync_league(platform_league_id, _fresh_players_cache())
        status = 'ok'
        detail = f"{summary.get('rosterCount', 0)} rosters, {len(summary.get('drafts', []))} draft(s)"
    except Exception as exc:  # pylint: disable=broad-exception-caught
        status = 'error'
        detail = str(exc)[:500]

    with SessionLocal() as session:
        run = session.get(SyncRun, run_id)
        if run is not None:
            run.status = status
            run.detail = detail
            run.finished_at = _utcnow()
            session.commit()
    return status


def leagues_to_sync() -> List[str]:
    """Every Sleeper league wuff knows: DB-imported plus the local config set."""
    ids: List[str] = []
    with SessionLocal() as session:
        for (platform_league_id,) in session.query(DbLeague.platform_league_id).filter_by(platform='sleeper'):
            ids.append(platform_league_id)
    for entry in load_sleeper_leagues_config().get('leagues', []):
        league_id = entry.get('leagueId')
        if league_id and league_id not in ids:
            ids.append(league_id)
    return ids


def sync_all_due() -> int:
    """The periodic sweep: sync every known league. Returns count attempted."""
    ids = leagues_to_sync()
    for platform_league_id in ids:
        sync_one_league(platform_league_id)
    return len(ids)


def ensure_scheduler_started() -> bool:
    """Start the background scheduler once per process. False when disabled."""
    global _scheduler  # pylint: disable=global-statement
    if _scheduler_disabled():
        return False
    if _scheduler is not None:
        return True
    with _scheduler_lock:
        if _scheduler is None:
            scheduler = BackgroundScheduler(daemon=True)
            scheduler.add_job(
                sync_all_due,
                'interval',
                minutes=SYNC_INTERVAL_MINUTES,
                id='sleeper-sync-sweep',
                next_run_time=datetime.now() + timedelta(seconds=60),
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            _scheduler = scheduler
    return True


def queue_league_sync(platform_league_id: str) -> bool:
    """Sync a league in the background; inline fallback when scheduler disabled.
    Returns True when queued, False when it ran inline."""
    if ensure_scheduler_started() and _scheduler is not None:
        _scheduler.add_job(
            sync_one_league,
            args=[platform_league_id],
            id=f'sleeper-sync-{platform_league_id}',
            replace_existing=True,
            misfire_grace_time=300,
        )
        return True
    sync_one_league(platform_league_id)
    return False
