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


engine = create_engine(
    DATABASE_URL,
    # SQLite objects to cross-thread use by default; Flask's dev server is threaded.
    connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {},
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
