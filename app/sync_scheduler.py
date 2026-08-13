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
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler

from . import espn_manager
from .crypto import decrypt_value
from .db import SessionLocal
from .free_rankings import refresh_free_rankings
from .nfl_stats import current_nfl_season, fetch_and_save_rosters
from .models import DbLeague, EspnCredential, SyncRun
from .paths import RAW_NFL_ROSTERS_DIR, SLEEPER_PLAYERS_CACHE_FILE
from .player_store import rebuild_registry
from .sleeper_manager import (
    load_players_cache,
    load_sleeper_leagues_config,
    refresh_players_cache,
    sync_league,
)

logger = logging.getLogger(__name__)

SYNC_INTERVAL_MINUTES = int(os.environ.get('SLEEPER_SYNC_INTERVAL_MINUTES', '360'))
PLAYERS_CACHE_MAX_AGE_HOURS = int(os.environ.get('SLEEPER_PLAYERS_CACHE_MAX_AGE_HOURS', '168'))
# Position resolution needs a roster snapshot per season, and the league
# analyses that use it only go back this far (see CLAUDE.md) -- fetching
# earlier seasons would be dead weight.
NFL_ROSTER_FIRST_SEASON = int(os.environ.get('NFL_ROSTER_FIRST_SEASON', '2022'))

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


def _espn_sync_args(platform_league_id: str) -> Tuple[int, Optional[str], Optional[str]]:
    """Season + decrypted cookies for an ESPN league (cookies None when the
    league is public / no credential stored)."""
    season = datetime.now().year
    espn_s2 = swid = None
    with SessionLocal() as session:
        row = session.query(DbLeague).filter_by(platform='espn', platform_league_id=platform_league_id).one_or_none()
        if row is not None and row.season:
            try:
                season = int(row.season)
            except ValueError:
                pass
        credential = (
            session.query(EspnCredential)
            .filter_by(platform_league_id=platform_league_id)
            .order_by(EspnCredential.created_at.desc())
            .first()
        )
    if credential is not None:
        try:
            espn_s2 = decrypt_value(credential.espn_s2_encrypted)
            swid = decrypt_value(credential.swid_encrypted)
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # key rotated; try public access
    return season, espn_s2, swid


def sync_one_league(platform: str, platform_league_id: str) -> str:
    """Sync one league's snapshots, recording a SyncRun. Returns final status."""
    with SessionLocal() as session:
        run = SyncRun(platform=platform, platform_league_id=platform_league_id, status='running')
        session.add(run)
        session.commit()
        run_id = run.id

    try:
        if platform == 'espn':
            season, espn_s2, swid = _espn_sync_args(platform_league_id)
            summary = espn_manager.sync_league(platform_league_id, season, espn_s2=espn_s2, swid=swid)
        else:
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


def leagues_to_sync() -> List[Tuple[str, str]]:
    """Every syncable league wuff knows, as (platform, platform_league_id):
    DB-imported Sleeper + ESPN leagues plus the local Sleeper config set."""
    pairs: List[Tuple[str, str]] = []
    with SessionLocal() as session:
        rows = (
            session.query(DbLeague.platform, DbLeague.platform_league_id)
            .filter(DbLeague.platform.in_(['sleeper', 'espn']))
            .all()
        )
        pairs.extend((platform, platform_league_id) for platform, platform_league_id in rows)
    for entry in load_sleeper_leagues_config().get('leagues', []):
        league_id = entry.get('leagueId')
        if league_id and ('sleeper', league_id) not in pairs:
            pairs.append(('sleeper', league_id))
    return pairs


def sync_all_due() -> int:
    """The periodic sweep: sync every known league. Returns count attempted."""
    pairs = leagues_to_sync()
    for platform, platform_league_id in pairs:
        sync_one_league(platform, platform_league_id)
    return len(pairs)


def refresh_rankings_job() -> None:
    """Daily free-sources rankings/ADP refresh (see app/free_rankings.py)."""
    try:
        refresh_free_rankings()
    except Exception:  # pylint: disable=broad-exception-caught
        # Transient API failure; yesterday's board stays in place. Logged
        # rather than swallowed -- a silently skipped refresh looks exactly
        # like a working one from the outside.
        logger.exception('free-rankings refresh failed')


def refresh_nfl_rosters_job() -> None:
    """Fetch the nflverse roster CSVs that fantasy_position_map() reads.

    Nothing else fetches these -- they were only ever written by the manual
    `fetch-nfl-stats` CLI, so a deployed container had none and
    fantasy_position_map() returned {} forever. That fails *silently*: the
    draft-patterns page renders "no draft history", and
    compute_historical_qb_pick_targets() finds no positions and returns [],
    so the rankings board quietly ships without the QB adjustment.

    Only the roster CSVs (~1MB/season), not the weekly/seasonal stats -- the
    position map is all the web app needs. Existing files are left alone
    except the current season, which is still changing.
    """
    current = current_nfl_season()
    missing = [
        season for season in range(NFL_ROSTER_FIRST_SEASON, current + 1)
        if season == current or not (RAW_NFL_ROSTERS_DIR / f'{season}.csv').exists()
    ]
    logger.info('nfl-rosters job: dir=%s seasons=%s', RAW_NFL_ROSTERS_DIR, missing or 'none needed')
    if not missing:
        return
    try:
        written = fetch_and_save_rosters(missing)
        logger.info('nfl-rosters job: wrote %s', sorted(written) or 'nothing')
    except Exception:  # pylint: disable=broad-exception-caught
        # Whatever is already on disk stays. Logged, not swallowed: an empty
        # position map degrades silently (see the docstring above), so a
        # failure here must be visible in the logs or it is undebuggable.
        logger.exception('nfl-rosters fetch failed')


def refresh_schedule_job() -> None:
    """Fetch the current season's nflverse schedule CSV (Phase 5 step 5).

    Same failure shape as refresh_nfl_rosters_job's docstring: nothing else
    fetches this, so a deployed container had no schedule and every bye week
    resolved through the committed data/config/nfl_bye_weeks.json snapshot
    only -- correct through the season this shipped in, silently stale for
    trades/relocations in a later one. The committed snapshot stays as the
    fallback (bye_weeks.bye_week_map() prefers a live CSV when present); this
    job is what keeps the live copy from going missing on a fresh container.
    Only the current season -- past seasons don't change, and the committed
    snapshot already covers them.
    """
    from .bye_weeks import fetch_and_save_schedule  # pylint: disable=import-outside-toplevel

    season = current_nfl_season()
    try:
        path = fetch_and_save_schedule(season)
        logger.info('schedule job: wrote %s', path)
    except Exception:  # pylint: disable=broad-exception-caught
        # The committed bye-weeks snapshot stays in place either way. Logged
        # rather than swallowed for the same reason as the sibling jobs above:
        # a stale schedule degrades silently (a rescheduled bye reads wrong,
        # not missing), so a failure here has to be visible in the logs.
        logger.exception('schedule fetch failed')


def refresh_player_registry_job() -> None:
    """Rebuild the cross-platform player identity registry (Phase 5 step 1).

    Runs after the nfl-rosters job so it can pick up the nflverse CSVs when
    they exist, but it does not need them: the Sleeper players cache alone
    carries espn_id/yahoo_id/gsis_id for every player, and that cache is the
    one source a deployed container can always refetch. _fresh_players_cache()
    is called first because a container that follows no Sleeper league never
    otherwise downloads it, and an absent cache builds an empty registry that
    resolves nothing.
    """
    try:
        _fresh_players_cache()
        stats = rebuild_registry()
        logger.info('player-registry job: %s players written (%s sleeper, %s nflverse-only, %s id conflicts)',
                    stats.get('written'), stats.get('sleeper'),
                    stats.get('nflverse_only'), stats.get('id_conflicts'))
    except Exception:  # pylint: disable=broad-exception-caught
        # The stored registry stays in place. Logged rather than swallowed:
        # an empty or stale registry degrades silently -- names simply stop
        # resolving -- which is the failure mode this whole phase exists to end.
        logger.exception('player-registry rebuild failed')


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
            scheduler.add_job(
                refresh_rankings_job,
                'interval',
                hours=24,
                id='free-rankings-daily',
                next_run_time=datetime.now() + timedelta(seconds=120),
                max_instances=1,
                coalesce=True,
            )
            # Ahead of the rankings refresh: that job's QB adjustment needs
            # the position map these CSVs back.
            scheduler.add_job(
                refresh_nfl_rosters_job,
                'interval',
                hours=24,
                id='nfl-rosters-daily',
                next_run_time=datetime.now() + timedelta(seconds=30),
                max_instances=1,
                coalesce=True,
            )
            # Last of the daily jobs: it reads what the two above write.
            scheduler.add_job(
                refresh_player_registry_job,
                'interval',
                hours=24,
                id='player-registry-daily',
                next_run_time=datetime.now() + timedelta(seconds=180),
                max_instances=1,
                coalesce=True,
            )
            # Independent of the others (no reader depends on it yet, and it
            # reads nothing they write) -- schedule is small (~285 rows/season)
            # so a fixed offset is enough, no need to chain it after anything.
            scheduler.add_job(
                refresh_schedule_job,
                'interval',
                hours=24,
                id='nfl-schedule-daily',
                next_run_time=datetime.now() + timedelta(seconds=90),
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            _scheduler = scheduler
    return True


def queue_league_sync(platform_league_id: str, platform: str = 'sleeper') -> bool:
    """Sync a league in the background; inline fallback when scheduler disabled.
    Returns True when queued, False when it ran inline."""
    if ensure_scheduler_started() and _scheduler is not None:
        _scheduler.add_job(
            sync_one_league,
            args=[platform, platform_league_id],
            id=f'{platform}-sync-{platform_league_id}',
            replace_existing=True,
            misfire_grace_time=300,
        )
        return True
    sync_one_league(platform, platform_league_id)
    return False
