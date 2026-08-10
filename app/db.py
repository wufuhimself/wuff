"""SQLite/SQLAlchemy foundation for multi-user state (Phase 1 of docs/roadmap.md).

League *snapshots* (rosters, drafts, rankings) stay in their JSON layout
behind app/repository.py; this DB holds what those files can't: user
accounts and which leagues each user follows. DATABASE_URL overrides the
default local SQLite file (that's the Postgres path in production —
call sites never change, only the URL).
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .paths import DATA_DIR

DEFAULT_DB_URL = f'sqlite:///{DATA_DIR / "wuff.db"}'
DATABASE_URL = os.environ.get('DATABASE_URL', DEFAULT_DB_URL)


class Base(DeclarativeBase):
    pass


engine = create_engine(
    DATABASE_URL,
    # SQLite objects to cross-thread use by default; Flask's dev server is threaded.
    connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)  # pylint: disable=invalid-name


def init_db() -> None:
    from . import models  # pylint: disable=import-outside-toplevel,unused-import,cyclic-import
    Base.metadata.create_all(engine)
