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
# tables, so init_db backfills these on SQLite. (Alembic replaces this before
# any production deploy.)
_COLUMN_BACKFILLS = [
    ('leagues', 'rules_json', 'VARCHAR(4000)'),
    # Pre-existing rows have no action -- backfill default matches their old,
    # only-possible meaning ("this player is kept").
    ('keeper_marks', 'action', "VARCHAR(8) NOT NULL DEFAULT 'include'"),
]


def _ensure_columns() -> None:
    if not DATABASE_URL.startswith('sqlite'):
        return
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
