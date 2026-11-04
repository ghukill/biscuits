"""biscuits' own SQLite database (biscuits.db in the data dir, mode 600).

It holds what OpenProject doesn't: state that belongs to biscuits itself. The
schema is managed by Alembic. The database is brought up to date the first time
it's used, so upgrading biscuits needs no separate migration step.
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import DateTime, Integer, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from . import config

MIGRATIONS = Path(__file__).parent / "migrations"

_lock = threading.Lock()
_engines: dict[Path, Engine] = {}


class Base(DeclarativeBase):
    pass


class ArchivedProject(Base):
    """A project archived in biscuits. OpenProject itself isn't touched."""

    __tablename__ = "archived_projects"

    project_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    archived_at = mapped_column(DateTime, nullable=False)


def engine() -> Engine:
    path = config.load().db_file
    with _lock:
        if path not in _engines:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))  # private from the first byte
            eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 15})
            _upgrade(eng)
            _engines[path] = eng
        return _engines[path]


def _upgrade(eng: Engine) -> None:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


@contextmanager
def session():
    """A session that commits on success and rolls back on an exception."""
    with Session(engine(), expire_on_commit=False) as s, s.begin():
        yield s
