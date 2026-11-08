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
from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine
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
    archived_by: Mapped[int | None] = mapped_column(Integer)


class SectionPosition(Base):
    """Where a section sits in its project. See sections.py."""

    __tablename__ = "section_positions"

    section_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at = mapped_column(DateTime, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer)


class User(UserMixin, Base):
    """A biscuits account, linked to the OpenProject user it acts as."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # biscuits admin: separate from being an OpenProject admin.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Part of the login session, so bumping it signs the person out everywhere.
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    op_user_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    op_user_name: Mapped[str | None] = mapped_column(String(255))
    op_key_enc: Mapped[str | None] = mapped_column(Text)  # encrypted; see accounts.py
    created_at = mapped_column(DateTime, nullable=False)
    last_login_at = mapped_column(DateTime)
    disabled_at = mapped_column(DateTime)

    @property
    def is_active(self):
        return self.disabled_at is None

    def get_id(self):
        return f"{self.id}:{self.session_version}"


class ApiToken(Base):
    """A token that lets a program act as a biscuits account over MCP. Only a
    hash is kept; the token itself is shown once, when it's made. See tokens.py."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at = mapped_column(DateTime, nullable=False)
    last_used_at = mapped_column(DateTime)
    revoked_at = mapped_column(DateTime)


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
