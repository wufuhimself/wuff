"""SQLite/SQLAlchemy foundation for multi-user state (Phase 1 of docs/roadmap.md).

League *snapshots* (rosters, drafts, rankings) stay in their JSON layout
behind app/repository.py; this DB holds what those files can't: user
accounts and which leagues each user follows. DATABASE_URL overrides the
default local SQLite file (that's the Postgres path in production —
call sites never change, only the URL).
"""
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .paths import DATA_DIR

DEFAULT_DB_URL = f'sqlite:///{DATA_DIR / "wuff.db"}'
DATABASE_URL = os.environ.get('DATABASE_URL', DEFAULT_DB_URL)
# Railway (and Heroku before it) hand out `postgres://`, a scheme SQLAlchemy
# 2.x's psycopg dialect lookup rejects outright ("Can't load plugin:
# sqlalchemy.dialects:postgres") -- normalize it rather than let a copied
# env var 500 the app on first request.
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)


class Base(DeclarativeBase):
    pass


_IS_SQLITE = DATABASE_URL.startswith('sqlite')

engine = create_engine(
    DATABASE_URL,
    # SQLite objects to cross-thread use by default; Flask's dev server is threaded.
    connect_args={'check_same_thread': False} if _IS_SQLITE else {},
    # Postgres only. A pooled connection can be dead by the time it is reused
    # -- Railway's proxy drops idle ones, and a database restart kills them
    # all -- and psycopg2 only finds out mid-query, surfacing as
    # "SSL SYSCALL error: EOF detected" on a random page. pre_ping costs one
    # trivial round trip per checkout and retries transparently; recycle
    # retires connections before the proxy's idle timeout can. Neither
    # applies to SQLite, which has no server to disconnect from.
    **({} if _IS_SQLITE else {'pool_pre_ping': True, 'pool_recycle': 280}),
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)  # pylint: disable=invalid-name


# Columns added after a table first shipped; create_all won't ALTER existing
# tables, so init_db backfills these. (Alembic replaces this eventually.)
# Runs on Postgres too, not just SQLite: production's tables were created by an
# earlier deploy's create_all, so a column added later would exist in the model
# and not in the database -- and the failure is an UndefinedColumn 500 on the
# first query touching it, i.e. every page. Each entry is skipped when the
# column is already present, so this is a no-op on a freshly created database.
_COLUMN_BACKFILLS = [
    ('leagues', 'rules_json', 'VARCHAR(4000)'),
    # Pre-existing rows have no action -- backfill default matches their old,
    # only-possible meaning ("this player is kept").
    ('keeper_marks', 'action', "VARCHAR(8) NOT NULL DEFAULT 'include'"),
    # NULL for every pre-existing user: no stored preference, so membership.py
    # falls back to their first followed league.
    ('users', 'default_league_slug', 'VARCHAR(80)'),
    # NULL for marks made before franchise identity existed; keeper_service
    # falls back to matching on team_name, which is what those rows have
    # always used. Backfilled by `python3 -m app build-franchises`.
    ('keeper_marks', 'franchise_id', 'VARCHAR(160)'),
    # True for every pre-existing league -- they were all still syncing
    # before this column existed, so "keep syncing" is the correct default,
    # not just a schema convenience.
    ('leagues', 'active', 'BOOLEAN NOT NULL DEFAULT TRUE'),
    # 'scheduled' for every pre-existing row -- see SyncRun.trigger's
    # docstring for why that's the correct historical default, not just a
    # schema convenience.
    ('sync_runs', 'trigger', "VARCHAR(16) NOT NULL DEFAULT 'scheduled'"),
]


def _ensure_columns() -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl_type in _COLUMN_BACKFILLS:
        if table not in tables:
            continue
        existing = {c['name'] for c in inspector.get_columns(table)}
        if column not in existing:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'))


def init_db() -> None:
    from . import models  # pylint: disable=import-outside-toplevel,unused-import,cyclic-import
    _ensure_columns()
    Base.metadata.create_all(engine)
